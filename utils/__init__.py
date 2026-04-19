import re
import unicodedata


def norm(value) -> str:
    if value is None:
        return ""
    value = str(value)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u3000", " ").replace("\xa0", " ")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value

