"""Generation de QCM a partir du texte extrait du PDF du cours."""

from __future__ import annotations

import logging
import re
from typing import Any

from pypdf import PdfReader

logger = logging.getLogger(__name__)

_MAX_QUESTIONS = 60
_MAX_CHOICE_LEN = 320
_MIN_OPTIONS = 2
_MIN_SENTENCE_LEN = 28


def _truncate(text: str, max_len: int = _MAX_CHOICE_LEN) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def extract_pdf_text(path: str) -> str:
    try:
        reader = PdfReader(path)
    except Exception as exc:
        logger.warning("Lecture PDF impossible: %s", exc)
        return ""
    parts: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text()
        except Exception:
            t = ""
        if t:
            parts.append(t)
    return "\n".join(parts)


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    raw = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for s in raw:
        s = s.strip()
        if len(s) < _MIN_SENTENCE_LEN:
            continue
        out.append(_truncate(s))
    return out


def _paragraph_fallback(text: str) -> list[str]:
    paras = []
    for block in text.split("\n\n"):
        b = re.sub(r"\s+", " ", block).strip()
        if len(b) >= _MIN_SENTENCE_LEN:
            paras.append(_truncate(b))
    return paras


def _candidates_from_text(text: str) -> list[str]:
    sents = _split_sentences(text)
    if len(sents) >= 4:
        return sents[:80]
    paras = _paragraph_fallback(text)
    return paras[:40]


def _answer_key_indexes(line: str) -> list[int]:
    m = re.search(
        r"(?:Réponse|Reponse|Corrige|Corrigé|Answer)\s*:\s*([A-Ea-e]{1,5})\b",
        line,
    )
    if not m:
        return []
    return _answer_letters_indexes(m.group(1))


def _extract_answer_map(text: str) -> dict[int, list[int]]:
    """Extrait une table de correction globale du type: `1 b`, `2: ac`, `3 - bd`."""
    answer_map: dict[int, list[int]] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    in_answers_section = False
    for line in lines:
        if re.search(r"\b(?:Réponses|Reponses|Answers|Corrige|Corrigé)\b", line, flags=re.I):
            in_answers_section = True

        if not in_answers_section:
            continue

        m = re.match(r"^(?:Q\s*)?(\d{1,3})\s*[\)\.\-:;]?\s*([A-Ea-e]{1,5})\b", line, flags=re.I)
        if not m:
            m = re.match(
                r"^(?:Q\s*)?(\d{1,3}).{0,20}(?:Réponse|Reponse|Answer)\s*[:\-]?\s*([A-Ea-e]{1,5})\b",
                line,
                flags=re.I,
            )
        if not m:
            continue

        q_num = int(m.group(1))
        indexes = []
        for ch in m.group(2).upper():
            idx = ord(ch.lower()) - ord("a")
            if 0 <= idx < 4 and idx not in indexes:
                indexes.append(idx)
        if indexes:
            answer_map[q_num] = indexes
    return answer_map


def _is_answers_header(line: str) -> bool:
    s = line.strip()
    return bool(
        re.fullmatch(r"(?:Réponses|Reponses|Answers|Corrige|Corrigé)\s*:?", s, flags=re.I)
    )


def _is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith("***") and s.endswith("***"):
        return True
    if re.match(r"^--\s*\d+\s+of\s+\d+\s*--$", s, flags=re.I):
        return True
    if re.match(r"^CORRECTION[-\s]", s, flags=re.I):
        return True
    if re.match(r"^(?:Contacts?\s*:|N°\s*d[’']ordre)", s, flags=re.I):
        return True
    return False


def _extract_inline_answer_token(stem: str) -> tuple[str, list[int]]:
    m = re.search(r"\s([A-Ea-e]{1,5})\s*$", stem)
    if not m:
        return stem.strip(), []
    token = m.group(1).upper()
    cleaned = stem[: m.start()].strip()
    if not cleaned or len(token) > 5:
        return stem.strip(), []
    return cleaned, _answer_letters_indexes(token)


def _answer_letters_index(line: str) -> int | None:
    s = line.strip().upper()
    if re.fullmatch(r"[A-E]{1,5}", s):
        return ord(s[0].lower()) - ord("a")
    return None


def _answer_letters_indexes(line: str) -> list[int]:
    s = line.strip().upper()
    if not re.fullmatch(r"[A-E]{1,5}", s):
        return []
    out: list[int] = []
    for ch in s:
        idx = ord(ch.lower()) - ord("a")
        if idx not in out:
            out.append(idx)
    return out


def _looks_like_question_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^\d+[\.)]\s+.+", s):
        return True
    if re.match(r"^\d+\s+.+", s) and not re.match(r"^\d+\s+[a-eA-E][\.)]\s+", s):
        return True
    if s.endswith("?"):
        return True
    return False


def _is_question_start_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^\d+[\.)]?(?:\s+.+)?$", s):
        return True
    return s.endswith("?")


def _parse_numbered_blocks(text: str) -> list[dict[str, Any]]:
    raw_lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in raw_lines if ln and not _is_noise_line(ln)]
    answer_map = _extract_answer_map(text)

    # Find question starts sequentially to avoid page-break noise.
    starts: list[tuple[int, int, str]] = []  # (q_num, line_index, inline_head)
    search_from = 0
    for q_num in range(1, _MAX_QUESTIONS + 1):
        pat = re.compile(rf"^{q_num}(?:[\.)]?\s*(.*))?$")
        found_idx = -1
        found_head = ""
        for idx in range(search_from, len(lines)):
            line = lines[idx]
            if _is_answers_header(line):
                break
            m = pat.match(line)
            if not m:
                continue
            found_idx = idx
            found_head = (m.group(1) or "").strip()
            break
        if found_idx < 0:
            continue
        starts.append((q_num, found_idx, found_head))
        search_from = found_idx + 1

    blocks: list[tuple[int, list[str], str]] = []
    for i, (q_num, idx, inline_head) in enumerate(starts):
        next_idx = starts[i + 1][1] if i + 1 < len(starts) else len(lines)
        body = lines[idx + 1 : next_idx]
        blocks.append((q_num, body, inline_head))

    out: list[dict[str, Any]] = []
    for q_num, body_lines, head_text in blocks:
        stem_parts: list[str] = []
        inline_indices: list[int] = []
        if head_text:
            cleaned_head, inline_indices = _extract_inline_answer_token(head_text)
            stem_parts.append(cleaned_head)

        opts: list[str] = []
        key_idx: int | None = inline_indices[0] if inline_indices else None
        key_indices: list[int] = list(inline_indices)
        for line in body_lines:
            if re.match(r"^NB\s*[:：]", line, flags=re.I):
                continue
            m_opt = re.match(r"^[a-eA-E][\.)]\s+(.+)$", line)
            if m_opt:
                opts.append(_truncate(m_opt.group(1).strip()))
                continue
            ak_indexes = _answer_key_indexes(line)
            if ak_indexes:
                key_idx = ak_indexes[0]
                key_indices = list(ak_indexes)
                continue
            ans_indexes = _answer_letters_indexes(line)
            if ans_indexes:
                key_idx = ans_indexes[0]
                key_indices = ans_indexes
                continue
            if opts:
                opts[-1] = _truncate(f"{opts[-1]} {line}".strip())
            else:
                stem_parts.append(line)

        stem = _truncate(" ".join(part for part in stem_parts if part).strip())
        if not stem or len(opts) < _MIN_OPTIONS:
            continue
        if key_idx is None:
            mapped = answer_map.get(q_num, [])
            if mapped:
                key_idx = mapped[0]
                key_indices = list(mapped)
        if key_idx is not None and not key_indices:
            key_indices = [key_idx]
        if key_idx is None:
            continue
        while len(opts) < 4:
            opts.append("(option supplementaire non fournie)")
        opts = opts[:4]
        if key_idx >= len(opts):
            continue
        out.append(
            {
                "prompt": stem,
                "choices": opts,
                "correct_index": key_idx,
                "correct_indices": key_indices,
            }
        )
        if len(out) >= _MAX_QUESTIONS:
            break
    return out


def _try_parse_structured(text: str) -> list[dict[str, Any]]:
    """Repere des blocs robustes question/options/reponse dans les PDFs de correction."""
    raw_lines = [ln.strip() for ln in text.splitlines()]
    answer_map = _extract_answer_map(text)
    questions: list[dict[str, Any]] = []
    i = 0
    fallback_num = 1

    while i < len(raw_lines):
        line = raw_lines[i].strip()
        if not line or _is_noise_line(line) or re.match(r"^NB\s*[:：]", line, flags=re.I):
            i += 1
            continue
        if _is_answers_header(line):
            break

        q_num: int | None = None
        inline_indices: list[int] = []
        stem_parts: list[str] = []

        m_num_only = re.match(r"^(\d+)$", line)
        m_num_line = re.match(r"^(\d+)[\.)]?\s+(.+)$", line)
        if m_num_only:
            q_num = int(m_num_only.group(1))
            i += 1
            while i < len(raw_lines):
                cur = raw_lines[i].strip()
                if (
                    not cur
                    or _is_noise_line(cur)
                    or re.match(r"^NB\s*[:：]", cur, flags=re.I)
                ):
                    i += 1
                    continue
                if re.match(r"^[a-eA-E][\.)]\s+(.+)$", cur):
                    break
                if _is_answers_header(cur):
                    break
                stem_parts.append(cur)
                i += 1
        elif m_num_line and not re.match(r"^\d+\s+[a-eA-E][\.)]\s+", line):
            q_num = int(m_num_line.group(1))
            stem_candidate, inline_indices = _extract_inline_answer_token(m_num_line.group(2).strip())
            stem_parts.append(stem_candidate)
            i += 1
        elif line.endswith("?"):
            q_num = fallback_num
            stem_candidate, inline_indices = _extract_inline_answer_token(line)
            stem_parts.append(stem_candidate)
            i += 1
        else:
            i += 1
            continue

        stem = _truncate(" ".join(part for part in stem_parts if part).strip())
        if not stem:
            continue

        opts: list[str] = []
        key_idx: int | None = inline_indices[0] if inline_indices else None
        key_indices: list[int] = list(inline_indices)
        pending_key_idx: int | None = None

        while i < len(raw_lines):
            cur = raw_lines[i].strip()
            if not cur or _is_noise_line(cur) or re.match(r"^NB\s*[:：]", cur, flags=re.I):
                i += 1
                continue
            if _is_answers_header(cur):
                break
            if opts and _is_question_start_line(cur):
                break

            m_opt = re.match(r"^[a-eA-E][\.)]\s+(.+)$", cur)
            if m_opt:
                opts.append(_truncate(m_opt.group(1).strip()))
                i += 1
                continue

            if opts:
                ak_indexes = _answer_key_indexes(cur)
                if ak_indexes:
                    if len(opts) >= _MIN_OPTIONS:
                        key_idx = ak_indexes[0]
                        key_indices = list(ak_indexes)
                        i += 1
                        break
                    pending_key_idx = ak_indexes[0]
                    i += 1
                    continue
                ans_indexes = _answer_letters_indexes(cur)
                if ans_indexes:
                    if len(opts) >= _MIN_OPTIONS:
                        key_idx = ans_indexes[0]
                        key_indices = ans_indexes
                        i += 1
                        break
                    pending_key_idx = ans_indexes[0]
                    i += 1
                    continue
                opts[-1] = _truncate(f"{opts[-1]} {cur}".strip())
                i += 1
                continue

            i += 1

        if len(opts) < _MIN_OPTIONS:
            continue

        if key_idx is None and pending_key_idx is not None:
            key_idx = pending_key_idx
        if key_idx is None:
            lookup_num = q_num if q_num is not None else fallback_num
            mapped = answer_map.get(lookup_num, [])
            if mapped:
                key_idx = mapped[0]
                key_indices = list(mapped)
        if key_idx is not None and not key_indices:
            key_indices = [key_idx]
        if key_idx is None or key_idx >= len(opts):
            continue

        while len(opts) < 4:
            opts.append("(option supplementaire non fournie)")
        opts = opts[:4]

        questions.append(
            {
                "prompt": stem,
                "choices": opts,
                "correct_index": key_idx,
                "correct_indices": key_indices,
            }
        )
        fallback_num += 1
        if len(questions) >= _MAX_QUESTIONS:
            break

    return questions


def _build_comprehension_questions(text: str, *, rng_seed: int) -> list[dict[str, Any]]:
    """QCM de comprehension : entierement deterministe (meme PDF + meme seed => meme quiz)."""
    pool = _candidates_from_text(text)
    if len(pool) < 4:
        return []

    pool_len = len(pool)
    n_questions = min(_MAX_QUESTIONS, max(4, pool_len // 3))
    n_questions = min(n_questions, pool_len - 1)
    if n_questions < 1:
        return []

    chosen: list[int] = []
    for p in range(n_questions):
        if n_questions <= 1:
            idx = 0
        else:
            idx = (p * (pool_len - 1)) // (n_questions - 1)
        idx = min(max(0, idx), pool_len - 1)
        if idx not in chosen:
            chosen.append(idx)
    while len(chosen) < n_questions:
        for j in range(pool_len):
            if j not in chosen:
                chosen.append(j)
                break
        else:
            break

    out: list[dict[str, Any]] = []
    for pos, idx in enumerate(chosen[:n_questions]):
        correct = pool[idx]
        wrong: list[str] = []
        j = (idx + 1 + pos) % pool_len
        guard = 0
        while len(wrong) < 3 and guard < pool_len * 4:
            guard += 1
            if j != idx:
                cand = pool[j]
                if cand != correct and cand not in wrong:
                    wrong.append(cand)
            j = (j + 1) % pool_len
        if len(wrong) < 3:
            continue

        correct_index = (abs(rng_seed) + pos * 3 + idx * 7) % 4
        choices_list: list[str] = []
        wi = 0
        for i in range(4):
            if i == correct_index:
                choices_list.append(_truncate(correct))
            else:
                choices_list.append(_truncate(wrong[wi]))
                wi += 1
        out.append(
            {
                "prompt": (
                    "Laquelle de ces affirmations est tiree du document de cours "
                    "(ou en reproduit une formulation tres proche) ?"
                ),
                "choices": choices_list,
                "correct_index": correct_index,
            }
        )
    return out


def build_questions_from_text(text: str, *, rng_seed: int) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    structured = _try_parse_structured(text)
    numbered = _parse_numbered_blocks(text)
    best = numbered if len(numbered) > len(structured) else structured
    if best:
        return best[:_MAX_QUESTIONS]

    # Fallback: when the PDF is not in explicit quiz format,
    # generate deterministic comprehension MCQs from extracted text.
    return _build_comprehension_questions(text, rng_seed=rng_seed)[:_MAX_QUESTIONS]


def build_questions_from_pdf_path(path: str, *, rng_seed: int) -> list[dict[str, Any]]:
    return build_questions_from_text(extract_pdf_text(path), rng_seed=rng_seed)


def rebuild_course_quiz(course) -> int:
    """Supprime les anciennes questions et regenere depuis le PDF. Retourne le nombre de questions."""
    from .models import CourseQuizQuestion

    CourseQuizQuestion.objects.filter(course_id=course.pk).delete()
    if not course.pdf_file:
        return 0
    try:
        path = course.pdf_file.path
    except Exception:
        logger.warning("Chemin fichier PDF introuvable pour le cours %s", course.pk)
        return 0
    items = build_questions_from_pdf_path(path, rng_seed=10_000 + (course.pk or 0))
    bulk = [
        CourseQuizQuestion(
            course=course,
            order=i,
            prompt=item["prompt"],
            choices=item["choices"],
            correct_index=int(item["correct_index"]),
            correct_indices=[
                int(idx)
                for idx in item.get("correct_indices", [item["correct_index"]])
                if 0 <= int(idx) < 4
            ],
        )
        for i, item in enumerate(items)
        if isinstance(item.get("choices"), list)
        and len(item["choices"]) == 4
        and 0 <= int(item["correct_index"]) < 4
    ]
    CourseQuizQuestion.objects.bulk_create(bulk)
    return len(bulk)


def rebuild_monthly_exam_quiz(exam) -> int:
    """Regenere les questions d'examen mensuel depuis le PDF. Retourne le nombre de questions."""
    if not exam.pdf_file:
        return 0
    try:
        path = exam.pdf_file.path
    except Exception:
        logger.warning("Chemin fichier PDF introuvable pour l'examen %s", exam.pk)
        return 0
    items = build_questions_from_pdf_path(path, rng_seed=20_000 + (exam.pk or 0))
    questions_list = [
        {
            "text": item["prompt"],
            "type": "multiple_select"
            if len(item.get("correct_indices", [item["correct_index"]])) > 1
            else "multiple_choice",
            "options": item["choices"],
            "correct_answer": (
                [int(idx) for idx in item.get("correct_indices", []) if 0 <= int(idx) < 4]
                if len(item.get("correct_indices", [item["correct_index"]])) > 1
                else int(item["correct_index"])
            ),
        }
        for item in items
        if isinstance(item.get("choices"), list)
        and len(item["choices"]) == 4
        and 0 <= int(item["correct_index"]) < 4
    ]
    exam.questions = questions_list
    exam.save(update_fields=["questions"])
    return len(questions_list)
