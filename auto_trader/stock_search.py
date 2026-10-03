"""Local stock-name matching. Add reviewed aliases by stock code without model training."""
from functools import lru_cache
import unicodedata

# Keep aliases explicit: similar company names must not share a guessed nickname.
STOCK_ALIASES = {
    "005930": ("삼전", "Samsung", "Samsung Electronics"),
    "000660": ("SK Hynix", "Hynix"),
    "005380": ("현차", "Hyundai Motor", "Hyundai"),
    "035420": ("네이버", "Naver"),
    "035720": ("Kakao",),
    "005490": ("포스코", "Posco", "Posco Holdings"),
    "051910": ("엘지화학", "LG Chem"),
    "006400": ("삼성에스디아이", "Samsung SDI"),
    "066570": ("엘지전자", "LG Electronics"),
    "105560": ("케이비금융", "KB Financial"),
    "055550": ("신한금융", "Shinhan", "Shinhan Financial"),
}
_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_INITIAL_KEYS = ("r", "R", "s", "e", "E", "f", "a", "q", "Q", "t", "T", "d", "w", "W", "c", "z", "x", "v", "g")
_VOWEL_KEYS = ("k", "o", "i", "O", "j", "p", "u", "P", "h", "hk", "ho", "hl", "y", "n", "nj", "np", "nl", "b", "m", "ml", "l")
_FINAL_KEYS = ("", "r", "R", "rt", "s", "sw", "sg", "e", "f", "fr", "fa", "fq", "ft", "fx", "fv", "fg", "a", "q", "qt", "t", "T", "d", "w", "c", "z", "x", "v", "g")


def normalize(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold()
                   if char.isalnum())


@lru_cache(maxsize=8192)
def name_forms(value: str) -> tuple[str, str, str, str]:
    initials, keys, initial_keys = [], [], []
    for char in value:
        offset = ord(char) - 0xAC00
        if 0 <= offset < 11172:
            lead, remainder = divmod(offset, 588)
            vowel, tail = divmod(remainder, 28)
            initials.append(_INITIALS[lead])
            keys.append(_INITIAL_KEYS[lead] + _VOWEL_KEYS[vowel] + _FINAL_KEYS[tail])
            initial_keys.append(_INITIAL_KEYS[lead])
        else:
            initials.append(char)
            keys.append(char)
            initial_keys.append(char)
    return tuple(normalize(form) for form in (value, "".join(initials), "".join(keys), "".join(initial_keys)))


def stock_match_rank(stock: dict, query: str) -> int | None:
    """Exact identity/alias first, then name fragments, keyboard input and initials."""
    if not query:
        return 0
    symbol = normalize(str(stock.get("symbol", "")))
    name, initials, keyboard, initial_keyboard = name_forms(str(stock.get("name", "")))
    aliases = tuple(normalize(alias) for alias in STOCK_ALIASES.get(str(stock.get("symbol", "")), ()))
    if query == symbol or query == name or query in aliases:
        return 0
    if query in symbol or query in name or any(query in alias for alias in aliases):
        return 1
    if query in keyboard:
        return 2
    if query in initials or query in initial_keyboard:
        return 3
    return None
