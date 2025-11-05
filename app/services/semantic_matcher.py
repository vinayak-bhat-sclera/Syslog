# app/services/semantic_matcher.py
import logging
import re
from typing import List, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger("app.services.semantic_matcher")

# lazy load model
try:
    MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    logger.info("SentenceTransformer loaded")
except Exception:
    MODEL = None
    logger.exception("Failed to load SentenceTransformer")


def _normalize_text(s: str) -> str:
    """Lowercase and collapse whitespace for robust substring checks."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def compute_semantic_match(message: str, keyword_list: List[str]) -> Tuple[Optional[str], float]:
    """
    Compute semantic similarity between `message` and each keyword in `keyword_list`.
    Returns (best_keyword, score) if model available; otherwise (None, 0.0).
    """
    if not keyword_list or MODEL is None:
        return None, 0.0
    try:
        kw_embs = MODEL.encode(keyword_list, convert_to_numpy=True, normalize_embeddings=True)
        msg_emb = MODEL.encode([message], convert_to_numpy=True, normalize_embeddings=True)[0]
        sims = cosine_similarity([msg_emb], kw_embs)[0]
        max_idx = int(np.argmax(sims))
        max_score = float(sims[max_idx])
        if max_score >= settings.SIMILARITY_THRESHOLD:
            return keyword_list[max_idx], max_score
        return None, max_score
    except Exception as e:
        logger.exception("Semantic matching error: %s", e)
        return None, 0.0


def find_matching_keyword(message: str, keyword_list: List[str]) -> Tuple[Optional[str], float]:
    """
    First attempt a cheap substring match (case-insensitive).
    If none found and sentence-transformer model is available, fall back to semantic match.
    Returns (matched_keyword, score). For substring matches score=1.0.
    """
    if not keyword_list:
        return None, 0.0

    norm_msg = _normalize_text(message)
    # substring (exact phrase) match — fast and preferred
    for kw in keyword_list:
        if not kw:
            continue
        if _normalize_text(kw) in norm_msg:
            return kw, 1.0

    # fallback to semantic similarity if model present
    kw_sem, score = compute_semantic_match(message, keyword_list)
    if kw_sem:
        return kw_sem, score

    return None, score
