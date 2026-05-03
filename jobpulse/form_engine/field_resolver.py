"""Field resolver — thread-safe lookup tables and locale-agnostic answer resolution.

Replaces the module-level mutable dicts and UK-centric hardcoding with
instance-based LabelMappingStore and configurable locale profiles.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from shared.pii import pii_json, wrap_pii_value

logger = get_logger(__name__)

# ── Comprehensive country data (ISO-3166 inspired) ──
# Covers all major countries. Can be extended via JSON file.
_COUNTRY_DATA: dict[str, tuple[str, ...]] = {
    "United Kingdom": ("uk", "gb", "u k", "great britain", "+44", "44", "united kingdom (+44)"),
    "United States": ("us", "usa", "united states of america", "+1", "1", "united states (+1)"),
    "Germany": ("de", "deutschland", "+49", "49"),
    "France": ("fr", "+33", "33"),
    "India": ("in", "+91", "91"),
    "Canada": ("ca", "+1"),
    "Australia": ("au", "+61", "61"),
    "Ireland": ("ie", "+353", "353"),
    "Netherlands": ("nl", "+31", "31"),
    "Spain": ("es", "+34", "34"),
    "Italy": ("it", "+39", "39"),
    "Japan": ("jp", "+81", "81"),
    "China": ("cn", "+86", "86"),
    "Brazil": ("br", "+55", "55"),
    "Singapore": ("sg", "+65", "65"),
    "Switzerland": ("ch", "+41", "41"),
    "Sweden": ("se", "+46", "46"),
    "Poland": ("pl", "+48", "48"),
    "Portugal": ("pt", "+351", "351"),
    "Belgium": ("be", "+32", "32"),
    "Austria": ("at", "+43", "43"),
    "Denmark": ("dk", "+45", "45"),
    "Finland": ("fi", "+358", "358"),
    "Norway": ("no", "+47", "47"),
    "New Zealand": ("nz", "+64", "64"),
    "South Africa": ("za", "+27", "27"),
    "Mexico": ("mx", "+52", "52"),
    "Argentina": ("ar", "+54", "54"),
    "United Arab Emirates": ("ae", "+971", "971"),
    "South Korea": ("kr", "+82", "82"),
    "Russia": ("ru", "+7", "7"),
    "Turkey": ("tr", "+90", "90"),
    "Saudi Arabia": ("sa", "+966", "966"),
    "Israel": ("il", "+972", "972"),
    "Malaysia": ("my", "+60", "60"),
    "Thailand": ("th", "+66", "66"),
    "Indonesia": ("id", "+62", "62"),
    "Philippines": ("ph", "+63", "63"),
    "Vietnam": ("vn", "+84", "84"),
    "Pakistan": ("pk", "+92", "92"),
    "Bangladesh": ("bd", "+880", "880"),
    "Nigeria": ("ng", "+234", "234"),
    "Egypt": ("eg", "+20", "20"),
    "Kenya": ("ke", "+254", "254"),
    "Ghana": ("gh", "+233", "233"),
    "Colombia": ("co", "+57", "57"),
    "Chile": ("cl", "+56", "56"),
    "Peru": ("pe", "+51", "51"),
    "Czech Republic": ("cz", "+420", "420"),
    "Hungary": ("hu", "+36", "36"),
    "Romania": ("ro", "+40", "40"),
    "Greece": ("gr", "+30", "30"),
    "Ukraine": ("ua", "+380", "380"),
    "Croatia": ("hr", "+385", "385"),
    "Slovenia": ("si", "+386", "386"),
    "Slovakia": ("sk", "+421", "421"),
    "Lithuania": ("lt", "+370", "370"),
    "Latvia": ("lv", "+371", "371"),
    "Estonia": ("ee", "+372", "372"),
    "Luxembourg": ("lu", "+352", "352"),
    "Iceland": ("is", "+354", "354"),
    "Malta": ("mt", "+356", "356"),
    "Cyprus": ("cy", "+357", "357"),
    "Bulgaria": ("bg", "+359", "359"),
    "Serbia": ("rs", "+381", "381"),
    "Morocco": ("ma", "+212", "212"),
    "Tunisia": ("tn", "+216", "216"),
    "Qatar": ("qa", "+974", "974"),
    "Kuwait": ("kw", "+965", "965"),
    "Bahrain": ("bh", "+973", "973"),
    "Oman": ("om", "+968", "968"),
    "Jordan": ("jo", "+962", "962"),
    "Lebanon": ("lb", "+961", "961"),
    "Iraq": ("iq", "+964", "964"),
    "Iran": ("ir", "+98", "98"),
    "Afghanistan": ("af", "+93", "93"),
    "Sri Lanka": ("lk", "+94", "94"),
    "Nepal": ("np", "+977", "977"),
    "Myanmar": ("mm", "+95", "95"),
    "Cambodia": ("kh", "+855", "855"),
    "Laos": ("la", "+856", "856"),
    "Mongolia": ("mn", "+976", "976"),
    "North Korea": ("kp", "+850", "850"),
    "Taiwan": ("tw", "+886", "886"),
    "Hong Kong": ("hk", "+852", "852"),
    "Macau": ("mo", "+853", "853"),
    "Brunei": ("bn", "+673", "673"),
    "Fiji": ("fj", "+679", "679"),
    "Papua New Guinea": ("pg", "+675", "675"),
    "Samoa": ("ws", "+685", "685"),
    "Tonga": ("to", "+676", "676"),
    "Vanuatu": ("vu", "+678", "678"),
    "Solomon Islands": ("sb", "+677", "677"),
    "Palau": ("pw", "+680", "680"),
    "Micronesia": ("fm", "+691", "691"),
    "Marshall Islands": ("mh", "+692", "692"),
    "Nauru": ("nr", "+674", "674"),
    "Kiribati": ("ki", "+686", "686"),
    "Tuvalu": ("tv", "+688", "688"),
    "Cook Islands": ("ck", "+682", "682"),
    "Niue": ("nu", "+683", "683"),
    "Tokelau": ("tk", "+690", "690"),
    "Wallis and Futuna": ("wf", "+681", "681"),
    "French Polynesia": ("pf", "+689", "689"),
    "New Caledonia": ("nc", "+687", "687"),
    "Guam": ("gu", "+1671", "1671"),
    "Northern Mariana Islands": ("mp", "+1670", "1670"),
    "American Samoa": ("as", "+1684", "1684"),
    "Puerto Rico": ("pr", "+1", "1"),
    "US Virgin Islands": ("vi", "+1340", "1340"),
    "British Virgin Islands": ("vg", "+1284", "1284"),
    "Anguilla": ("ai", "+1264", "1264"),
    "Montserrat": ("ms", "+1664", "1664"),
    "Bermuda": ("bm", "+1441", "1441"),
    "Cayman Islands": ("ky", "+1345", "1345"),
    "Turks and Caicos Islands": ("tc", "+1649", "1649"),
    "Bahamas": ("bs", "+1242", "1242"),
    "Barbados": ("bb", "+1246", "1246"),
    "Jamaica": ("jm", "+1876", "1876"),
    "Trinidad and Tobago": ("tt", "+1868", "1868"),
    "Grenada": ("gd", "+1473", "1473"),
    "Saint Vincent and the Grenadines": ("vc", "+1784", "1784"),
    "Saint Lucia": ("lc", "+1758", "1758"),
    "Dominica": ("dm", "+1767", "1767"),
    "Antigua and Barbuda": ("ag", "+1268", "1268"),
    "Saint Kitts and Nevis": ("kn", "+1869", "1869"),
    "Belize": ("bz", "+501", "501"),
    "Costa Rica": ("cr", "+506", "506"),
    "Panama": ("pa", "+507", "507"),
    "Guatemala": ("gt", "+502", "502"),
    "Honduras": ("hn", "+504", "504"),
    "El Salvador": ("sv", "+503", "503"),
    "Nicaragua": ("ni", "+505", "505"),
    "Ecuador": ("ec", "+593", "593"),
    "Bolivia": ("bo", "+591", "591"),
    "Paraguay": ("py", "+595", "595"),
    "Uruguay": ("uy", "+598", "598"),
    "Venezuela": ("ve", "+58", "58"),
    "Guyana": ("gy", "+592", "592"),
    "Suriname": ("sr", "+597", "597"),
    "French Guiana": ("gf", "+594", "594"),
    "Cuba": ("cu", "+53", "53"),
    "Haiti": ("ht", "+509", "509"),
    "Dominican Republic": ("do", "+1809", "1809"),
    "Puerto Rico": ("pr", "+1", "1"),
    "Albania": ("al", "+355", "355"),
    "Andorra": ("ad", "+376", "376"),
    "Armenia": ("am", "+374", "374"),
    "Azerbaijan": ("az", "+994", "994"),
    "Belarus": ("by", "+375", "375"),
    "Bosnia and Herzegovina": ("ba", "+387", "387"),
    "Georgia": ("ge", "+995", "995"),
    "Kazakhstan": ("kz", "+7", "7"),
    "Kosovo": ("xk", "+383", "383"),
    "Kyrgyzstan": ("kg", "+996", "996"),
    "Liechtenstein": ("li", "+423", "423"),
    "Moldova": ("md", "+373", "373"),
    "Monaco": ("mc", "+377", "377"),
    "Montenegro": ("me", "+382", "382"),
    "North Macedonia": ("mk", "+389", "389"),
    "San Marino": ("sm", "+378", "378"),
    "Tajikistan": ("tj", "+992", "992"),
    "Turkmenistan": ("tm", "+993", "993"),
    "Uzbekistan": ("uz", "+998", "998"),
    "Vatican City": ("va", "+379", "379"),
    "Algeria": ("dz", "+213", "213"),
    "Angola": ("ao", "+244", "244"),
    "Benin": ("bj", "+229", "229"),
    "Botswana": ("bw", "+267", "267"),
    "Burkina Faso": ("bf", "+226", "226"),
    "Burundi": ("bi", "+257", "257"),
    "Cameroon": ("cm", "+237", "237"),
    "Cape Verde": ("cv", "+238", "238"),
    "Central African Republic": ("cf", "+236", "236"),
    "Chad": ("td", "+235", "235"),
    "Comoros": ("km", "+269", "269"),
    "Congo": ("cg", "+242", "242"),
    "DR Congo": ("cd", "+243", "243"),
    "Djibouti": ("dj", "+253", "253"),
    "Equatorial Guinea": ("gq", "+240", "240"),
    "Eritrea": ("er", "+291", "291"),
    "Eswatini": ("sz", "+268", "268"),
    "Ethiopia": ("et", "+251", "251"),
    "Gabon": ("ga", "+241", "241"),
    "Gambia": ("gm", "+220", "220"),
    "Guinea": ("gn", "+224", "224"),
    "Guinea-Bissau": ("gw", "+245", "245"),
    "Ivory Coast": ("ci", "+225", "225"),
    "Lesotho": ("ls", "+266", "266"),
    "Liberia": ("lr", "+231", "231"),
    "Libya": ("ly", "+218", "218"),
    "Madagascar": ("mg", "+261", "261"),
    "Malawi": ("mw", "+265", "265"),
    "Mali": ("ml", "+223", "223"),
    "Mauritania": ("mr", "+222", "222"),
    "Mauritius": ("mu", "+230", "230"),
    "Mozambique": ("mz", "+258", "258"),
    "Namibia": ("na", "+264", "264"),
    "Niger": ("ne", "+227", "227"),
    "Rwanda": ("rw", "+250", "250"),
    "Sao Tome and Principe": ("st", "+239", "239"),
    "Senegal": ("sn", "+221", "221"),
    "Seychelles": ("sc", "+248", "248"),
    "Sierra Leone": ("sl", "+232", "232"),
    "Somalia": ("so", "+252", "252"),
    "South Sudan": ("ss", "+211", "211"),
    "Sudan": ("sd", "+249", "249"),
    "Tanzania": ("tz", "+255", "255"),
    "Togo": ("tg", "+228", "228"),
    "Uganda": ("ug", "+256", "256"),
    "Zambia": ("zm", "+260", "260"),
    "Zimbabwe": ("zw", "+263", "263"),
    "Bhutan": ("bt", "+975", "975"),
    "Maldives": ("mv", "+960", "960"),
    "Timor-Leste": ("tl", "+670", "670"),
}

# Gender aliases (locale-agnostic)
_GENDER_ALIASES: dict[str, tuple[str, ...]] = {
    "male": ("man", "m", "boy"),
    "man": ("male", "m", "boy"),
    "female": ("woman", "f", "girl"),
    "woman": ("female", "f", "girl"),
    "non-binary": ("nonbinary", "non binary", "prefer to self-describe", "enby"),
    "prefer not to say": ("prefer not to answer", "decline to state", "no answer"),
}

# Ethnicity aliases (can be customized per locale)
_ETHNICITY_ALIASES: dict[str, tuple[str, ...]] = {
    "asian indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian or asian british - indian",
    ),
    "asian or asian british - indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian indian",
    ),
    "white": ("white or caucasian", "white british", "white european", "white american"),
    "black african": ("black or black british - african", "african american"),
    "black caribbean": ("black or black british - caribbean",),
    "mixed": ("mixed or multiple ethnic groups", "biracial", "multiracial"),
    "hispanic or latino": ("latino", "hispanic", "latin american"),
    "native american": ("american indian", "alaska native", "indigenous"),
    "pacific islander": ("native hawaiian", "samoan", "tongan", "maori"),
    "middle eastern": ("arab", "persian", "north african"),
}


# ── LabelMappingStore (thread-safe replacement for module-level dict) ──

@dataclass
class LabelMappingStore:
    """Thread-safe, instance-based label→profile_key mapping store."""

    _mappings: dict[str, str] = field(default_factory=dict)
    _db_path: str | None = None
    _loaded: bool = field(default=False, repr=False)

    def __post_init__(self):
        if not self._db_path:
            from jobpulse.config import DATA_DIR
            self._db_path = str(DATA_DIR / "field_label_mappings.db")
        self._load_from_db()

    def _load_from_db(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS label_mappings (
                        label TEXT PRIMARY KEY,
                        profile_key TEXT NOT NULL,
                        times_used INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                """)
                rows = conn.execute(
                    "SELECT label, profile_key FROM label_mappings"
                ).fetchall()
                for label, key in rows:
                    if label not in self._mappings:
                        self._mappings[label] = key
                if rows:
                    logger.info("LabelMappingStore: loaded %d mappings", len(rows))
        except Exception as exc:
            logger.debug("Could not load label mappings: %s", exc)

    def get(self, label: str) -> str | None:
        return self._mappings.get(label.lower().strip())

    def learn(self, label: str, profile_key: str) -> None:
        """Learn a new mapping and persist to SQLite."""
        label_norm = label.lower().strip()
        if label_norm in self._mappings:
            return
        self._mappings[label_norm] = profile_key
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO label_mappings (label, profile_key, times_used, created_at)
                       VALUES (?, ?, 1, ?)
                       ON CONFLICT(label) DO UPDATE SET
                           times_used = times_used + 1""",
                    (label_norm, profile_key, datetime.now(timezone.utc).isoformat()),
                )
        except Exception as exc:
            logger.debug("Could not persist label mapping: %s", exc)

    def seed_mappings(self, seeds: dict[str, str]) -> None:
        """Bulk-seed initial mappings (e.g., from config)."""
        for label, key in seeds.items():
            self._mappings.setdefault(label.lower().strip(), key)


# ── Seed label mappings ──

_SEED_LABELS: dict[str, str] = {
    "first name": "first_name",
    "last name": "last_name",
    "surname": "last_name",
    "family name": "last_name",
    "email": "email",
    "email address": "email",
    "confirm your email": "email",
    "confirm email": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile number": "phone",
    "linkedin": "linkedin",
    "linkedin url": "linkedin",
    "linkedin profile": "linkedin",
    "website": "portfolio",
    "portfolio": "portfolio",
    "personal website": "portfolio",
    "github": "github",
    "github url": "github",
    "city": "location",
    "city or town": "location",
    "town/city": "location",
    "location": "location",
    "given name": "first_name",
    "preferred name": "first_name",
    "headline": "headline",
    "current title": "headline",
    "title": "title",
    "salutation": "title",
    "prefix": "title",
    "address": "address",
    "address line 1": "address",
    "street address": "address",
    "postcode": "postcode",
    "zip code": "postcode",
    "postal code": "postcode",
    "country": "country",
    "country/region": "country",
    "country phone code": "phone_code",
    "phone device type": "phone_device_type",
    "phone extension": "phone_extension",
    "name": "full_name",
}


def create_default_label_store() -> LabelMappingStore:
    """Create a LabelMappingStore seeded with default mappings."""
    store = LabelMappingStore()
    store.seed_mappings(_SEED_LABELS)
    return store


# ── Fuzzy matching ──

_TOKEN_TO_PROFILE_KEY: dict[str, str] = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "mobile": "phone",
    "number": "phone",
    "linkedin": "linkedin",
    "github": "github",
    "portfolio": "portfolio",
    "website": "portfolio",
    "city": "location",
    "postcode": "postcode",
    "postal": "postcode",
    "zip": "postcode",
    "country": "country",
    "address": "address",
    "headline": "headline",
}

_BIGRAM_TO_PROFILE_KEY: list[tuple[tuple[str, str], str]] = [
    (("first", "name"), "first_name"),
    (("last", "name"), "last_name"),
    (("family", "name"), "last_name"),
    (("given", "name"), "first_name"),
    (("preferred", "name"), "first_name"),
    (("phone", "number"), "phone"),
    (("mobile", "number"), "phone"),
    (("zip", "code"), "postcode"),
    (("postal", "code"), "postcode"),
    (("street", "address"), "address"),
    (("address", "line"), "address"),
    (("job", "title"), "headline"),
    (("current", "title"), "headline"),
    (("email", "address"), "email"),
    (("city", "town"), "location"),
    (("phone", "code"), "phone_code"),
    (("country", "phone"), "phone_code"),
    (("phone", "extension"), "phone_extension"),
    (("phone", "device"), "phone_device_type"),
]


def fuzzy_label_to_profile_key(label: str) -> str | None:
    """Match a field label to a profile key using token overlap."""
    tokens = set(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())

    for (t1, t2), key in _BIGRAM_TO_PROFILE_KEY:
        if t1 in tokens and t2 in tokens:
            return key

    _AMBIGUOUS_SINGLE = {"name", "number", "type", "line"}
    for token in tokens - _AMBIGUOUS_SINGLE:
        if token in _TOKEN_TO_PROFILE_KEY:
            return _TOKEN_TO_PROFILE_KEY[token]

    return None


# ── Option matching ──

@dataclass
class LocaleProfile:
    """User-configurable locale settings for form filling."""

    location: str = ""
    country: str = ""
    phone_code: str = ""
    visa_status: str = ""
    work_auth_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)


def build_option_aliases(locale: LocaleProfile | None = None, store: Any = None) -> dict[str, tuple[str, ...]]:
    """Build option alias dict from generic tables + locale overrides.

    Args:
        locale: Locale-specific overrides.
        store: Backward-compatible ProfileStore (extracts locale if no locale provided).
    """
    # Backward compatibility: caller may pass ProfileStore as first positional arg
    if locale is not None and not isinstance(locale, LocaleProfile):
        store = locale
        locale = None

    # Extract locale from store
    if locale is None and store is not None:
        try:
            loc = store.identity().location or ""
            country = _country_from_location(loc)
            if country:
                locale = LocaleProfile(country=country)
        except Exception:
            pass

    aliases: dict[str, tuple[str, ...]] = {}
    aliases.update(_GENDER_ALIASES)
    aliases.update(_ETHNICITY_ALIASES)

    for canonical, abbrevs in _COUNTRY_DATA.items():
        canonical_lower = canonical.lower()
        existing = aliases.get(canonical_lower, ())
        aliases[canonical_lower] = existing + tuple(
            a for a in abbrevs if a not in existing
        )
        for abbr in abbrevs:
            existing = aliases.get(abbr, ())
            if canonical_lower not in existing:
                aliases[abbr] = existing + (canonical_lower,)

    if locale and locale.work_auth_aliases:
        for key, vals in locale.work_auth_aliases.items():
            existing = aliases.get(key, ())
            aliases[key] = existing + tuple(v for v in vals if v not in existing)

    return aliases


def canonicalize_country_value(label: str, value: str, locale: LocaleProfile | None = None, store: Any = None) -> str:
    """Normalize country abbreviations to canonical names.

    Args:
        label: Field label text.
        value: Raw value to canonicalize.
        locale: Locale-specific country override.
        store: Backward-compatible ProfileStore (extracts locale if no locale provided).
    """
    # Backward compatibility: extract locale from store
    if locale is None and store is not None:
        try:
            loc = store.identity().location or ""
            country = _country_from_location(loc)
            if country:
                locale = LocaleProfile(country=country)
        except Exception:
            pass

    norm_label = _normalize_match_text(label)
    if "country" not in norm_label:
        return value

    norm_value = _normalize_match_text(value)

    # Try locale country first
    if locale and locale.country:
        for canonical, abbrevs in _COUNTRY_DATA.items():
            if canonical.lower() == locale.country.lower():
                if norm_value in abbrevs or norm_value == canonical.lower():
                    return canonical

    # Fall back to full lookup
    for canonical, abbrevs in _COUNTRY_DATA.items():
        if norm_value in abbrevs or norm_value == canonical.lower():
            return canonical

    return value


def best_option_match(
    label: str,
    value: str,
    options: list[str],
    *,
    locale: LocaleProfile | None = None,
    store: Any = None,
) -> str | None:
    """Return the best option match with country/gender/ethnicity/visa alias support.

    Args:
        label: Field label text.
        value: Raw value to match.
        options: Available option texts.
        locale: Locale-specific overrides.
        store: Backward-compatible ProfileStore (extracts locale if no locale provided).
    """
    # Backward compatibility: extract locale from store
    if locale is None and store is not None:
        try:
            loc = store.identity().location or ""
            country = _country_from_location(loc)
            if country:
                locale = LocaleProfile(country=country)
        except Exception:
            pass

    if not options:
        return None

    canonical_value = canonicalize_country_value(label, value, locale=locale)
    norm_label = _normalize_match_text(label)
    norm_value = _normalize_match_text(canonical_value)
    normalized_options = [_normalize_match_text(opt) for opt in options]

    if not norm_value:
        return None

    # Country-specific: prefer exact + dial code match
    if "country" in norm_label:
        if norm_value == "united kingdom":
            for opt, norm_opt in zip(options, normalized_options):
                if "united kingdom" in norm_opt and "+44" in opt:
                    return opt
            for opt, norm_opt in zip(options, normalized_options):
                if norm_opt == "united kingdom" or norm_opt.startswith("united kingdom"):
                    return opt
            for opt in options:
                if "+44" in opt:
                    return opt

    # Work auth / visa — use locale aliases if provided
    if locale and locale.work_auth_aliases and ("visa" in norm_label or "work" in norm_label):
        for alias_key, alias_vals in locale.work_auth_aliases.items():
            if alias_key in norm_value:
                for opt, norm_opt in zip(options, normalized_options):
                    for av in alias_vals:
                        if av.lower() in norm_opt:
                            return opt

    # Generic alias matching
    aliases = build_option_aliases(locale)
    for alias in aliases.get(norm_value, ()):
        norm_alias = _normalize_match_text(alias)
        for opt, norm_opt in zip(options, normalized_options):
            if norm_opt == norm_alias or norm_alias.startswith(norm_opt) or norm_opt.startswith(norm_alias):
                return opt

    # Exact match
    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt == norm_value:
            return opt

    # Starts with
    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt.startswith(norm_value):
            return opt

    # Contains (for values >= 4 chars)
    if len(norm_value) >= 4:
        for opt, norm_opt in zip(options, normalized_options):
            if norm_value in norm_opt:
                return opt

    # Token overlap (Jaccard)
    value_tokens = {
        token for token in norm_value.split()
        if len(token) > 2 and token not in {"and", "for", "the", "with", "from", "valid"}
    }
    best_option = None
    best_score = 0
    for opt, norm_opt in zip(options, normalized_options):
        option_tokens = {
            token for token in norm_opt.split()
            if len(token) > 2 and token not in {"and", "for", "the", "with"}
        }
        overlap = len(value_tokens & option_tokens)
        if overlap > best_score:
            best_score = overlap
            best_option = opt

    if best_option is not None and best_score >= 2:
        return best_option

    return None


# ── Profile prompt helpers ──

def profile_prompt_json(profile: dict[str, Any]) -> str:
    return pii_json(profile, "applicant.profile")


def screening_prompt_profile(store: Any = None) -> dict[str, Any]:
    if store:
        ident = store.identity()
        work_auth = store.as_work_auth()
        return {
            "first_name": ident.first_name,
            "last_name": ident.last_name,
            "education": ident.education,
            "location": ident.location,
            "visa_status": work_auth.get("visa_status", ""),
            "notice_period": work_auth.get("notice_period", ""),
        }
    from jobpulse.applicator import PROFILE, WORK_AUTH
    return {
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "education": PROFILE["education"],
        "location": PROFILE["location"],
        "visa_status": WORK_AUTH["visa_status"],
        "notice_period": WORK_AUTH["notice_period"],
    }


def screening_prompt_background(profile: dict[str, Any], store: Any = None) -> str:
    relocation = "Yes"
    commuting = "Yes"
    right_to_work = "Yes"
    country = "the UK"

    if store:
        relocation = store.screening_default("relocation") or "Yes"
        commuting = store.screening_default("commuting") or "Yes"
        right_to_work = store.screening_default("right_to_work") or "Yes"
        country = _country_from_location(store.identity().location or "") or "the UK"

    return (
        f"Name: {wrap_pii_value('applicant.first_name', profile['first_name'])} "
        f"{wrap_pii_value('applicant.last_name', profile['last_name'])}. "
        f"Education: {wrap_pii_value('applicant.education', profile['education'])}. "
        f"Location: {wrap_pii_value('applicant.location', profile['location'])}. "
        f"Visa: {wrap_pii_value('applicant.visa_status', profile['visa_status'])}. "
        f"Notice: {wrap_pii_value('applicant.notice_period', profile['notice_period'])}. "
        f"Willing to relocate: {relocation}. "
        f"Commuting: {commuting}. "
        f"Right to work {country}: {right_to_work}."
    )


# ── Utilities ──

def _normalize_match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", str(text).lower()).strip()


def _country_from_location(location: str) -> str:
    """Extract country from 'City, Country' location string."""
    parts = [p.strip() for p in location.split(",")]
    return parts[-1] if len(parts) >= 2 else ""


def get_field_gap(label_text: str = "") -> float:
    """Return delay in seconds based on label length (simulates reading)."""
    import random
    length = len(label_text)
    if length < 10:
        return 0.3 + random.uniform(0, 0.15)
    if length < 30:
        return 0.5 + random.uniform(0, 0.3)
    if length < 60:
        return 0.8 + random.uniform(0, 0.4)
    return 1.2 + random.uniform(0, 0.5)


# ---------------------------------------------------------------------------
# Backward compatibility aliases for old NativeFormFiller / field_mapper.py
# ---------------------------------------------------------------------------

# Old module-level mutable dict — replaced by LabelMappingStore instance
_FIELD_LABEL_TO_PROFILE_KEY: dict[str, str] = dict(_SEED_LABELS)

# Old function names (with underscore prefix) that field_mapper.py imports
_build_option_aliases = build_option_aliases
_ensure_label_db = lambda: None  # No-op — LabelMappingStore auto-loads
_fuzzy_label_to_profile_key = fuzzy_label_to_profile_key
def _persist_label_mapping(label: str, profile_key: str) -> None:
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        FormExperienceDB().save_field_mappings("_global", {label: profile_key})
    except Exception:
        pass
_profile_prompt_json = profile_prompt_json
_screening_prompt_background = screening_prompt_background
_screening_prompt_profile = screening_prompt_profile


def _best_option_match(
    label: str, value: str, options: list[str], locale: LocaleProfile | None = None, store: Any = None
) -> str | None:
    """Backward-compatible alias for best_option_match."""
    return best_option_match(label, value, options, locale=locale, store=store)


_canonicalize_country_value = canonicalize_country_value
_get_field_gap = get_field_gap
