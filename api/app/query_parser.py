import re
from dataclasses import dataclass
from google.cloud import bigquery

FIELD_MAP = {
    "title": "title",
    "agency": "issuing_agency",
    "issuing_agency": "issuing_agency",
    "id": "source_reference_id",
    "ref": "source_reference_id",
    "reference": "source_reference_id",
    "source_reference_id": "source_reference_id",
    "desc": "description",
    "description": "description",
    "headline": "summary_headline",
    "summary": "summary_headline",
    "summary_headline": "summary_headline",
    "location": "location",
    "state": "location",
    "category": "category",
    "source": "source_id",
    "source_id": "source_id",
    "contact": "contact_name",
    "contact_name": "contact_name",
}

DEFAULT_TARGET_COLUMNS = [
    "title",
    "source_reference_id",
    "description",
    "summary_headline",
    "issuing_agency",
]

TOKEN_RE = re.compile(
    r'(?P<neg>-)?'
    r'(?:(?P<field>[a-zA-Z_]+):)?'
    r'(?:'
        r'"(?P<dquote>[^"]*)(?:"|$)'
        r"|'(?P<squote>[^']*)(?:'|$)"
        r'|(?P<word>\S+)'
    r')'
)


def escape_like_pattern(text: str) -> str:
    """Escape backslash and SQL LIKE wildcards (% and _)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def format_like_term(term: str, is_phrase: bool = False) -> str:
    """Format search term into a SQL LIKE pattern string.

    If not a quoted phrase and contains '*', user '*' translates to SQL '%'.
    Otherwise, non-wildcard terms are wrapped in '%term%' (with '\\', '%', '_' escaped).
    """
    if not is_phrase and "*" in term:
        parts = [escape_like_pattern(p) for p in term.split("*")]
        return "%".join(parts)
    return f"%{escape_like_pattern(term)}%"


@dataclass
class SearchToken:
    value: str
    field: str | None = None
    is_negated: bool = False
    is_phrase: bool = False


@dataclass
class ParsedQuery:
    conditions: list[str]
    parameters: list[bigquery.ScalarQueryParameter]
    order_clause: str | None


def parse_search_query(query_str: str) -> ParsedQuery:
    """Parse search query supporting:
    - Implicit AND between space-separated terms
    - Explicit uppercase OR
    - Negation via leading '-' or standalone uppercase 'NOT'
    - Exact phrases in double or single quotes
    - Field scoping via field:term (e.g. agency:transport, title:"road work")
    - Wildcard '*' translation to SQL '%'
    """
    clean_q = query_str.strip()
    if not clean_q:
        return ParsedQuery(conditions=[], parameters=[], order_clause=None)

    raw_tokens: list[tuple[str, str, str | None, bool]] = []
    for m in TOKEN_RE.finditer(clean_q):
        neg = bool(m.group("neg"))
        field_raw = m.group("field")
        dquote = m.group("dquote")
        squote = m.group("squote")
        word = m.group("word")

        if dquote is not None:
            raw_tokens.append(("PHRASE", dquote, field_raw, neg))
        elif squote is not None:
            raw_tokens.append(("PHRASE", squote, field_raw, neg))
        elif word is not None:
            if word == "OR" and not field_raw and not neg:
                raw_tokens.append(("OR", "OR", None, False))
            elif word == "AND" and not field_raw and not neg:
                raw_tokens.append(("AND", "AND", None, False))
            elif word == "NOT" and not field_raw and not neg:
                raw_tokens.append(("NOT", "NOT", None, False))
            else:
                raw_tokens.append(("WORD", word, field_raw, neg))

    and_groups: list[list[SearchToken]] = []
    current_or_group: list[SearchToken] = []
    pending_not = False
    pending_or = False

    for token_type, val, field_raw, neg in raw_tokens:
        if token_type == "AND":
            if current_or_group:
                and_groups.append(current_or_group)
                current_or_group = []
            pending_or = False
            continue

        if token_type == "OR":
            if current_or_group:
                pending_or = True
            continue

        if token_type == "NOT":
            pending_not = True
            continue

        field = FIELD_MAP.get(field_raw.lower()) if field_raw else None
        is_negated = neg or pending_not
        pending_not = False
        is_phrase = (token_type == "PHRASE")

        # Skip empty tokens
        if not val:
            continue

        token = SearchToken(
            value=val,
            field=field,
            is_negated=is_negated,
            is_phrase=is_phrase,
        )

        if pending_or:
            current_or_group.append(token)
            pending_or = False
        else:
            if current_or_group:
                and_groups.append(current_or_group)
            current_or_group = [token]

    if current_or_group:
        and_groups.append(current_or_group)

    if not and_groups:
        return ParsedQuery(conditions=[], parameters=[], order_clause=None)

    # Build SQL conditions and parameters
    params: list[bigquery.ScalarQueryParameter] = []
    conditions: list[str] = []
    param_counter = 0

    positive_tokens_with_param: list[tuple[SearchToken, str]] = []

    for or_group in and_groups:
        group_sql_parts = []
        for token in or_group:
            param_name = f"kw_{param_counter}"
            param_counter += 1
            param_value = format_like_term(token.value.lower(), is_phrase=token.is_phrase)
            params.append(bigquery.ScalarQueryParameter(param_name, "STRING", param_value))

            if not token.is_negated:
                positive_tokens_with_param.append((token, param_name))

            target_cols = [token.field] if token.field else DEFAULT_TARGET_COLUMNS

            if token.is_negated:
                # Exclude matches: NOT (LOWER(c1) LIKE @p OR LOWER(c2) LIKE @p ...)
                col_checks = [f"COALESCE(LOWER({c}), '') LIKE @{param_name}" for c in target_cols]
                term_sql = f"(NOT ({' OR '.join(col_checks)}))"
            else:
                col_checks = [f"LOWER({c}) LIKE @{param_name}" for c in target_cols]
                term_sql = f"({' OR '.join(col_checks)})"

            group_sql_parts.append(term_sql)

        if len(group_sql_parts) == 1:
            conditions.append(group_sql_parts[0])
        elif len(group_sql_parts) > 1:
            conditions.append(f"({' OR '.join(group_sql_parts)})")

    # Build relevance ORDER BY clause
    if not positive_tokens_with_param:
        order_clause = "ORDER BY first_seen_at DESC"
    elif len(positive_tokens_with_param) == 1:
        single_p = positive_tokens_with_param[0][1]
        order_clause = f"""
            ORDER BY
                CASE
                    WHEN LOWER(title) LIKE @{single_p} OR LOWER(source_reference_id) LIKE @{single_p} THEN 0
                    WHEN LOWER(summary_headline) LIKE @{single_p} THEN 1
                    ELSE 2
                END ASC,
                first_seen_at DESC
        """
    else:
        # Multiple positive terms: rank by full phrase match, all-in-title, any-in-title, headline
        full_param_name = "full_q"
        full_param_val = format_like_term(clean_q.lower(), is_phrase=True)
        params.append(bigquery.ScalarQueryParameter(full_param_name, "STRING", full_param_val))

        p_names = [p for _, p in positive_tokens_with_param]
        title_id_all = " AND ".join(
            f"(LOWER(title) LIKE @{p} OR LOWER(source_reference_id) LIKE @{p})"
            for p in p_names
        )
        title_id_any = " OR ".join(
            f"(LOWER(title) LIKE @{p} OR LOWER(source_reference_id) LIKE @{p})"
            for p in p_names
        )
        headline_any = " OR ".join(
            f"LOWER(summary_headline) LIKE @{p}"
            for p in p_names
        )

        order_clause = f"""
            ORDER BY
                CASE
                    WHEN LOWER(title) LIKE @{full_param_name} OR LOWER(source_reference_id) LIKE @{full_param_name} THEN 0
                    WHEN ({title_id_all}) THEN 1
                    WHEN ({title_id_any}) THEN 2
                    WHEN ({headline_any}) THEN 3
                    ELSE 4
                END ASC,
                first_seen_at DESC
        """

    return ParsedQuery(
        conditions=conditions,
        parameters=params,
        order_clause=order_clause,
    )
