from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RepoRecord:
    name: str
    owner: str
    url: str
    created_at: str
    language: Optional[str]
    stars: int
    description: str

    layer1_pass: bool = False
    layer1_reasons: list = field(default_factory=list)

    layer2_pass: bool = False
    layer2_reasons: list = field(default_factory=list)
    score: float = 0.0

    homepage: str = ""
    site_url: str = ""   # homepage > README抽出URL > vercel推定URL の優先順で決定

    layer3_result: str = ""   # "pass" | "uncertain" | "fail" | ""
    layer3_score: int = 0
    layer3_reasons: list = field(default_factory=list)

    layer5_pass: bool = False
    layer5_reasons: list = field(default_factory=list)
    layer5_confidence: float = 0.0
    layer5_lp_url: str = ""
    layer5_company_name: str = ""
    layer5_founder_name: str = ""
