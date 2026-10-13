"""The reviewer-comment patterns that count as a request to simplify.

Everything matched here is untrusted text from public repositories. It is only ever matched
against these patterns, never followed.

STRONG patterns name the request outright. WEAK ones are pushback that is usually about
simplification but sometimes isn't ("unused", "extract", "is this needed?"). A label resting
only on a WEAK match is marked so, and those are the first items offered for adjudication.
"""
import re

STRONG = re.compile(r"""(?ix)
  \bsimplif(?:y|ied|ies|ying|ication)\b
| \bsimpler\b
| \bover[- ]?engineer
| \boverkill\b
| \bover[- ]?complicat
| \bneedlessly\s+(?:complex|complicated)
| \bunnecessar(?:y|ily)\s+(?:complex|complicated|verbose|convoluted)
| \bduplicat(?:e|ed|es|ion|ing)\s+(?:code|logic|of|the|this|what|block|function|method)
| \b(?:code|logic)\s+(?:is\s+)?duplicat
| \bcopy[- ]?(?:and[- ]?)?past(?:e|ed)\b
| \bde-?dup(?:e|licate)\b
| \byagni\b
| \bdead\s+code\b
| \b(?:can'?t|cannot|can\s+not|never|won'?t|shouldn'?t\s+ever)\s+(?:ever\s+)?(?:happen|occur)\b
| \bcan(?:'?t|not)\s+be\s+(?:nil|null|none|empty|negative|reached)\b
| \bunreachable\b
| \btoo\s+(?:complex|complicated|convoluted|clever|deeply\s+nested)\b
| \b(?:function|method|class|this)\s+is\s+(?:getting\s+)?too\s+(?:long|big|large)\b
| \bconvoluted\b
| \bmore\s+(?:complicated|complex)\s+than\s+(?:it\s+needs|necessary|needed|required)
| \bredundant\b
| \bunnecessary\b
| \bno\s+need\s+(?:for|to)\b
| \b(?:is\s+)?not\s+(?:really\s+)?(?:needed|necessary)\b
| \bdebug(?:ging)?\s+(?:print|output|log|logging|statement|code)
| \bleft\s*over\b
""")

WEAK = re.compile(r"""(?ix)
  \bduplicat(?:e|ed|es|ion)\b
| \b(?:factor|pull|move|extract)\s+(?:this|these|that|it|them|the\s+\w+\s+)?\s*(?:out|into)\b
| \bextract(?:ing)?\s+(?:a|an|this|the)\s+(?:helper|function|method)
| \bhelper\s+(?:function|method)\b
| \b(?:is|are)\s+(?:this|that|these|it)\s+(?:really\s+)?(?:needed|necessary|required)\b
| \bdo\s+(?:we|you)\s+(?:really\s+)?need\s+(?:this|that|these|it)\b
| \bunused\b
| \b(?:hard|difficult)\s+to\s+(?:read|follow|understand)\b
| \bsingle[- ]use\b
| \bonly\s+(?:used|called)\s+(?:once|in\s+one\s+place|here)\b
| \binline\s+(?:this|it)\b
| \bcould\s+(?:just|simply)\s+be\b
| \b(?:can|could)\s+(?:just\s+)?replace\s+.{0,60}?\b(?:with|by)\b
| \bwhy\s+not\s+(?:just\s+)?(?:use|call|reuse)\b
| \breuse\b
| \bcan\s+(?:just|simply)\s+(?:be|use|call|return|do)\b
""")

ACK = re.compile(r"(?i)^\s*(done|fixed|removed|good (catch|point)|ack|agreed|simplified|changed|updated|refactored|extracted)\b")


def classify(text):
    """Returns 'strong', 'weak' or None for one comment's text."""
    if not text:
        return None
    if STRONG.search(text):
        return "strong"
    if WEAK.search(text):
        return "weak"
    return None
