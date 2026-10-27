"""校规知识库检索器（BM25 + jieba 分词）。

关键设计——**条款时效核验**（本项目的创新点之一）：

1. 每条条款携带 `effective_to`（失效日期），检索时若条款已过期，
   其排序分会被显著降权（乘以 0.25），并在该条命中上标注"已失效"；
2. 每条命中都返回 `doc_number` / `effective_from` / `source`，让答案**可核验**；
3. `is_sample=True` 的校内示例条款在返回结果中明确标注，避免与真实文件混淆；
4. 采用 BM25 而非向量检索，理由有三：
   - 零额外依赖、零外部 API 调用，**断网也能跑**；
   - 校规检索是典型的"术语精确匹配"场景，BM25 的词频饱和特性反而更准；
   - 检索结果可解释（命中了哪个词、权重多少），便于答辩。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import jieba

from core.config import get_settings
from core.schemas import PolicyClause, PolicyHit

# 停用词：中英文常见虚词，避免污染检索
STOPWORDS: set[str] = {
    "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "什么", "可以", "哪些", "怎么", "如何", "请问", "我想",
    "a", "an", "the", "of", "to", "is", "are", "and", "or", "in", "on", "for",
}

# 过期条款降权系数
EXPIRED_PENALTY = 0.25

# frontmatter 解析
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class _Doc:
    """检索用文档结构。"""

    clause: PolicyClause
    tokens: list[str]
    length: int
    freq: Counter


class PolicySearcher:
    """BM25 检索器。"""

    def __init__(self, knowledge_dir: Path | None = None) -> None:
        settings = get_settings()
        self.knowledge_dir = knowledge_dir or settings.knowledge_dir
        self.docs: list[_Doc] = []
        self.avg_len: float = 1.0
        self.k1: float = 1.5
        self.b: float = 0.75
        self._load()

    # -- 加载 ---------------------------------------------------------------

    def _load(self) -> None:
        """扫描知识库目录，解析所有条款。"""
        self.docs = []
        if not self.knowledge_dir.exists():
            return
        for path in sorted(self.knowledge_dir.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            clause = self._parse_file(path)
            if clause is None:
                continue
            tokens = self._tokenize(self._index_text(clause))
            self.docs.append(
                _Doc(
                    clause=clause,
                    tokens=tokens,
                    length=len(tokens),
                    freq=Counter(tokens),
                )
            )
        if self.docs:
            self.avg_len = sum(d.length for d in self.docs) / len(self.docs)

    @staticmethod
    def _parse_file(path: Path) -> PolicyClause | None:
        """解析 Markdown 文件：frontmatter 元数据 + 正文。"""
        raw = path.read_text(encoding="utf-8")
        match = _FM_RE.match(raw)
        if not match:
            return None

        meta: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

        body = raw[match.end():].strip()
        title = meta.get("title", path.stem)
        # 若正文首行是标题（如 "## 备考时间线"），去掉开头的 # 号但保留整段正文
        first_line = body.split("\n", 1)[0]
        if first_line.startswith("#"):
            rest = body.split("\n", 1)[1] if "\n" in body else ""
            body = first_line.lstrip("#").strip() + ("\n" + rest if rest else "")

        keywords_raw = meta.get("keywords", "")
        keywords = [
            k.strip() for k in re.split(r"[,，]", keywords_raw) if k.strip()
        ]

        clause = PolicyClause(
            clause_id=path.stem,
            title=title,
            category=meta.get("category", "其他"),
            doc_number=meta.get("doc_number", ""),
            effective_from=meta.get("effective_from", ""),
            effective_to=meta.get("effective_to", ""),
            source=meta.get("source", ""),
            body=body,
            is_sample=meta.get("is_sample", "false").strip().lower() == "true",
            keywords=keywords,
        )
        return clause

    @staticmethod
    def _index_text(clause: PolicyClause) -> str:
        """构造用于索引的文本：标题 + 关键词 + 正文（正文重复一次以提权）。"""
        return " ".join(
            [
                clause.title,
                clause.title,
                clause.category,
                clause.doc_number,
                " ".join(clause.keywords),
                " ".join(clause.keywords),
                clause.body,
            ]
        )

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """jieba 分词 + 去停用词 + 过滤单字符虚词。"""
        tokens: list[str] = []
        for word in jieba.cut(text):
            word = word.strip()
            if not word or word in STOPWORDS:
                continue
            if len(word) < 2 and not word.isdigit():
                continue
            tokens.append(word.lower())
        return tokens

    # -- 检索 ---------------------------------------------------------------

    def search(self, query: str, top_k: int = 5, category: str | None = None) -> list[PolicyHit]:
        """BM25 检索。

        Args:
            query: 查询关键词。
            top_k: 返回条数。
            category: 可选类别过滤。

        Returns:
            命中列表，已应用过期条款降权并标注状态。
        """
        if not self.docs or not query.strip():
            return []

        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []

        scores: list[tuple[float, _Doc]] = []
        for doc in self.docs:
            if category and doc.clause.category != category:
                continue
            score = self._bm25(q_tokens, doc)
            if score > 0:
                scores.append((score, doc))

        # 先应用时效降权，再排序——保证失效条款真正沉到结果末尾
        weighted: list[tuple[float, _Doc, str]] = []
        for score, doc in scores:
            note = ""
            if doc.clause.is_expired:
                score *= EXPIRED_PENALTY
                note = (
                    f"该条款已于 {doc.clause.effective_to} 失效，"
                    "已自动降权，请以学校官方最新文件为准"
                )
            elif doc.clause.is_sample:
                note = "校内演示样例条款（非真实校规），仅用于功能演示"
            weighted.append((score, doc, note))

        weighted.sort(key=lambda x: x[0], reverse=True)

        hits: list[PolicyHit] = []
        for score, doc, note in weighted[:top_k]:
            hits.append(
                PolicyHit(
                    clause=doc.clause,
                    score=round(score, 4),
                    note=note,
                )
            )
        return hits

    def _bm25(self, q_tokens: list[str], doc: _Doc) -> float:
        """标准 BM25 打分公式。"""
        n_docs = len(self.docs)
        score = 0.0
        for token in set(q_tokens):
            df = sum(1 for d in self.docs if token in d.freq)
            if df == 0:
                continue
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            tf = doc.freq.get(token, 0)
            denom = tf + self.k1 * (1 - self.b + self.b * doc.length / self.avg_len)
            score += idf * (tf * (self.k1 + 1)) / denom
        return score

    # -- 其它 ---------------------------------------------------------------

    def list_docs(self, category: str | None = None) -> list[PolicyClause]:
        """列出知识库中的条款目录。"""
        items = [d.clause for d in self.docs]
        if category:
            items = [c for c in items if c.category == category]
        return items

    def categories(self) -> list[str]:
        """列出所有类别。"""
        return sorted({d.clause.category for d in self.docs})

    def deadlines(self, keyword: str = "") -> list[dict[str, str]]:
        """按关键词提取各类事项的截止时间线索。

        从条款正文中匹配"截止""前提交""之前""时间"等上下文，抽取时间表述。
        这是一个轻量的启发式实现——不追求完备，追求**可用且可解释**。
        """
        patterns = [
            r"[^。；\n]*(?:截止|之前|以前|前提交|前报送|时间内|期间)[^。；\n]*",
        ]
        results: list[dict[str, str]] = []
        for doc in self.docs:
            if keyword and keyword not in doc.clause.title and keyword not in doc.clause.body:
                continue
            for line in doc.clause.body.splitlines():
                line = line.strip().lstrip("#").strip()
                if not line:
                    continue
                for pat in patterns:
                    for m in re.finditer(pat, line):
                        text = m.group(0).strip()
                        if len(text) < 6:
                            continue
                        results.append(
                            {
                                "title": doc.clause.title,
                                "text": text,
                                "doc_number": doc.clause.doc_number,
                                "source": doc.clause.source,
                                "status": doc.clause.status_label,
                                "is_sample": "true" if doc.clause.is_sample else "false",
                            }
                        )
        # 去重
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for r in results:
            key = f"{r['title']}::{r['text']}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)
        return unique


_searcher: PolicySearcher | None = None


def get_searcher() -> PolicySearcher:
    """获取检索器单例（首次调用时加载知识库）。"""
    global _searcher
    if _searcher is None:
        _searcher = PolicySearcher()
    return _searcher


def reload_searcher() -> PolicySearcher:
    """强制重新加载知识库（编辑条款后使用）。"""
    global _searcher
    _searcher = PolicySearcher()
    return _searcher
