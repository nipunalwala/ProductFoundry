"""Language of a review: `en`, `hinglish`, or the detected code (ARCHITECTURE.md 6.1).

Script first, then a Hinglish lexicon, then a statistical detector. The detector
alone is not trusted: it reads Hinglish as Tagalog, Swahili or Somali, and short
English as Dutch. Pure functions; the detector is seeded.
"""

import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0

UNDETERMINED = "und"

# Hindi function words and very common review words, as written in Latin script.
# Words that are also English or common in other languages (to, me, par, hi, main,
# band, se, no) are left out on purpose.
HINDI_WORDS = frozenset(
    """
    hai hain h hota hoti hote hua hui hue hoga hogi tha thi raha rahi rahe rha rhi rhe
    nahi nahin nhi nai mat kya kyu kyun kyon kaise kaisa kab kaha kahan kaun kitna kitni
    ka ki ke ko mein mai mujhe mujhko mera meri mere hum humko hamara hamari apna apni apne
    aap aapka aapki tum tumhara yeh ye woh wo isme usme isko usko iska iski uska uski inka
    aur ya lekin magar par phir fir bhi toh sirf bas bilkul ekdum bahut bohot bhot zyada jyada
    kuch sab sabhi koi kisi abhi ab tab jab kabhi hamesha roz baar wapas vapas
    kar karo karna karne karta karti karte kiya kiye kijiye karke krna kr
    de do dena deta deti diya diye dijiye le lo lena leta liya liye
    ja jao jana jata jati jate gaya gayi gaye aa aao aana aata aati aaya aayi aaye
    chal chalta chalti chalte chala chali ruk rukta ho hona hone
    accha acha achha badhiya badiya mast sahi theek thik galat kharab bekar bekaar bakwas
    ghatiya faltu bura wala wali wale paisa paise rupaye rupay kat kata gaya
    kaam kam samay dikkat pareshani samasya madad kripya dhanyavad shukriya
    bhai yaar dost sir ji log logo kyunki isliye taki agar warna
    pe pr tak saath sath bina liye baad pehle pahle andar bahar upar niche
    """.split()
)
_AMBIGUOUS = frozenset(
    {"kam", "par", "pe", "pr", "ho", "do", "de", "le", "lo", "ja", "aa", "h", "ab"}
)
HINDI_STRONG = HINDI_WORDS - _AMBIGUOUS

# Enough English to recognise a short English review the detector misreads.
ENGLISH_WORDS = frozenset(
    """
    the a an is are was were be been am it this that these those i you we they my your our
    and or but not no yes of to in on for with from at by as if so very too also just
    app apps good bad best worst great nice excellent poor awesome amazing terrible useless
    love like hate easy hard simple useful helpful working work works worked use using used
    please fix update updated issue issues problem problems bug bugs crash crashes slow fast
    money payment refund account login support service customer experience time
    cannot can't doesn't don't didn't won't isn't never always ever again still after before
    when what why how which who all any some more most much many only than then now
    """.split()
)

_TOKEN = re.compile(r"[a-z']+")
_MIN_HINDI_HITS = 2
_MIN_HINDI_SHARE = 0.15
_MIN_ENGLISH_SHARE = 0.2
_MIN_TOKENS_TO_TRUST_DETECTOR = 5


@dataclass(frozen=True)
class Detection:
    language: str  # the best label so far
    borderline: bool = False  # True: ask the LLM before relying on it


def detect_language(text: str) -> Detection:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return Detection(UNDETERMINED)
    devanagari = sum("ऀ" <= char <= "ॿ" for char in letters)
    latin = sum(char.isascii() for char in letters)
    if devanagari / len(letters) >= 0.2:
        return Detection("hi")
    if latin / len(letters) < 0.5:
        return Detection(_statistical(text))

    tokens = _TOKEN.findall(text.lower())
    if not tokens:
        return Detection(UNDETERMINED)
    hindi = sum(token in HINDI_STRONG for token in tokens)
    weak_hindi = sum(token in _AMBIGUOUS for token in tokens)
    english = sum(token in ENGLISH_WORDS for token in tokens)

    if hindi >= _MIN_HINDI_HITS and (hindi + weak_hindi) / len(tokens) >= _MIN_HINDI_SHARE:
        return Detection("hinglish")
    if hindi:
        # One Hindi word: an English review with a borrowed word, or thin Hinglish.
        return Detection("hinglish", borderline=True)

    detected = _statistical(text)
    if detected == "en" or english / len(tokens) >= _MIN_ENGLISH_SHARE:
        return Detection("en")
    if len(tokens) < _MIN_TOKENS_TO_TRUST_DETECTOR:
        return Detection("en", borderline=True)
    return Detection(detected)


def _statistical(text: str) -> str:
    try:
        return detect(text).split("-")[0]  # zh-cn -> zh
    except LangDetectException:
        return UNDETERMINED
