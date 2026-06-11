from typing import Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class ChatMessage:
    sender: str
    content: str
    msg_type: str
    msg_attr: str
    timestamp: str
    group_name: str
    sender_remark: str = ""
    quote_content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
