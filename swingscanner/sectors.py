"""
Hand-curated NSE symbol → sector mapping.

Covers the most liquid ~250 NSE names. Stocks not in this map return
"Unknown" — the report just omits the sector tag for them.
"""

from __future__ import annotations


SECTOR_MAPPING: dict[str, str] = {
    # Bank
    "HDFCBANK": "Bank", "ICICIBANK": "Bank", "SBIN": "Bank",
    "AXISBANK": "Bank", "KOTAKBANK": "Bank", "INDUSINDBK": "Bank",
    "BANKBARODA": "Bank", "PNB": "Bank", "FEDERALBNK": "Bank",
    "IDFCFIRSTB": "Bank", "RBLBANK": "Bank", "AUBANK": "Bank",
    "BANDHANBNK": "Bank", "CANBK": "Bank", "CSBBANK": "Bank",
    "KARURVYSYA": "Bank", "J&KBANK": "Bank", "KTKBANK": "Bank",
    "DCBBANK": "Bank", "EQUITASBNK": "Bank", "ESAFSFB": "Bank",
    "SURYODAY": "Bank", "UTKARSHBNK": "Bank",

    # IT
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    "COFORGE": "IT", "LTTS": "IT", "KPITTECH": "IT", "TATAELXSI": "IT",
    "OFSS": "IT", "BIRLASOFT": "IT", "ZENSARTECH": "IT", "TATATECH": "IT",
    "NETWEB": "IT", "HAPPSTMNDS": "IT", "INTELLECT": "IT",

    # Auto
    "MARUTI": "Auto", "M&M": "Auto", "TATAMOTORS": "Auto",
    "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "TVSMOTOR": "Auto", "ASHOKLEY": "Auto", "BHARATFORG": "Auto",
    "MOTHERSON": "Auto", "BOSCHLTD": "Auto", "ESCORTS": "Auto",
    "MRF": "Auto", "BALKRISIND": "Auto", "APOLLOTYRE": "Auto",
    "CEAT": "Auto", "EXIDEIND": "Auto", "RICOAUTO": "Auto",
    "LUMAXIND": "Auto", "ENDURANCE": "Auto",

    # Pharma / Healthcare
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "AUROPHARMA": "Pharma",
    "TORNTPHARM": "Pharma", "ALKEM": "Pharma", "AJANTPHARM": "Pharma",
    "BIOCON": "Pharma", "GLAND": "Pharma", "GLENMARK": "Pharma",
    "ZYDUSLIFE": "Pharma", "MANKIND": "Pharma", "GRANULES": "Pharma",
    "LAURUSLABS": "Pharma", "IPCALAB": "Pharma", "JBCHEPHARM": "Pharma",
    "METROPOLIS": "Healthcare", "THYROCARE": "Healthcare",
    "DRLALPATH": "Healthcare", "GLOBALHLTH": "Healthcare",
    "NARAYANHRUD": "Healthcare", "APOLLOHOSP": "Healthcare",
    "FORTIS": "Healthcare", "MAXHEALTH": "Healthcare",

    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "GODREJCP": "FMCG", "COLPAL": "FMCG", "EMAMILTD": "FMCG",
    "TATACONSUM": "FMCG", "VBL": "FMCG", "RADICO": "FMCG",
    "UBL": "FMCG", "UNITDSPR": "FMCG", "HONASA": "FMCG",

    # Metal
    "TATASTEEL": "Metal", "JSWSTEEL": "Metal", "HINDALCO": "Metal",
    "VEDL": "Metal", "JINDALSTEL": "Metal", "SAIL": "Metal",
    "NMDC": "Metal", "HINDCOPPER": "Metal", "NATIONALUM": "Metal",
    "JSL": "Metal", "APLAPOLLO": "Metal", "RATNAMANI": "Metal",
    "WELCORP": "Metal", "MOIL": "Metal", "MAHASTEEL": "Metal",

    # Energy
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
    "IOC": "Energy", "GAIL": "Energy", "PETRONET": "Energy",
    "COALINDIA": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "TATAPOWER": "Energy", "ADANIPOWER": "Energy", "ADANIGREEN": "Energy",
    "JSWENERGY": "Energy", "NHPC": "Energy", "SJVN": "Energy",
    "TORNTPOWER": "Energy", "IGL": "Energy", "MGL": "Energy",
    "GUJGASLTD": "Energy",

    # Realty
    "DLF": "Realty", "LODHA": "Realty", "GODREJPROP": "Realty",
    "OBEROIRLTY": "Realty", "PRESTIGE": "Realty", "BRIGADE": "Realty",
    "SOBHA": "Realty", "SUNTECK": "Realty", "MAHLIFE": "Realty",
    "ANANTRAJ": "Realty", "ELDEHSG": "Realty", "ASHIANA": "Realty",

    # Financial Services (non-bank)
    "BAJFINANCE": "Financial Svcs", "BAJAJFINSV": "Financial Svcs",
    "SHRIRAMFIN": "Financial Svcs", "MUTHOOTFIN": "Financial Svcs",
    "CHOLAFIN": "Financial Svcs", "MANAPPURAM": "Financial Svcs",
    "LICHSGFIN": "Financial Svcs", "PFC": "Financial Svcs",
    "RECLTD": "Financial Svcs", "POONAWALLA": "Financial Svcs",
    "HDFCAMC": "Financial Svcs", "NAM-INDIA": "Financial Svcs",
    "MOTILALOFS": "Financial Svcs", "ANGELONE": "Financial Svcs",
    "ANANDRATHI": "Financial Svcs", "BSE": "Financial Svcs",
    "MCX": "Financial Svcs", "CDSL": "Financial Svcs",
    "JIOFIN": "Financial Svcs", "IRFC": "Financial Svcs",
    "NUVAMA": "Financial Svcs",

    # Infra / Capital Goods
    "LT": "Infra", "RVNL": "Infra", "BEL": "Infra", "HAL": "Infra",
    "BHEL": "Infra", "BDL": "Infra", "BEML": "Infra",
    "CUMMINSIND": "Infra", "ABB": "Infra", "SIEMENS": "Infra",
    "THERMAX": "Infra", "KEC": "Infra", "KEI": "Infra",
    "POLYCAB": "Infra", "HAVELLS": "Infra", "VOLTAS": "Infra",
    "BLUESTARCO": "Infra", "GMRINFRA": "Infra", "ADANIPORTS": "Infra",
    "JSWINFRA": "Infra", "CONCOR": "Infra", "DIXON": "Infra",
    "TITAGARH": "Infra", "JWL": "Infra", "TEXRAIL": "Infra",
    "RAILTEL": "Infra", "INOXWIND": "Infra", "SUZLON": "Infra",
    "APAR": "Infra", "APARINDS": "Infra", "KIRLOSENG": "Infra",
    "CERA": "Infra", "BERGEPAINT": "Infra",

    # Consumption / Retail
    "TITAN": "Consumption", "TRENT": "Consumption", "DMART": "Consumption",
    "ASIANPAINT": "Consumption", "PIDILITIND": "Consumption",
    "PAGEIND": "Consumption", "BATAINDIA": "Consumption",
    "RELAXO": "Consumption", "ABFRL": "Consumption",
    "KALYANKJIL": "Consumption", "SENCO": "Consumption",
    "VMART": "Consumption", "INDIAMART": "Consumption",
    "NYKAA": "Consumption", "ZOMATO": "Consumption", "SWIGGY": "Consumption",

    # Media
    "ZEEL": "Media", "SUNTV": "Media", "TIPS": "Media",
    "SAREGAMA": "Media", "PVRINOX": "Media", "NAZARA": "Media",

    # Chemicals
    "GUJALKALI": "Chemicals", "DEEPAKNTR": "Chemicals",
    "AARTIIND": "Chemicals", "TATACHEM": "Chemicals",
    "PIIND": "Chemicals", "UPL": "Chemicals", "SRF": "Chemicals",
    "NAVINFLUOR": "Chemicals", "ALKYLAMINE": "Chemicals",
    "VINATIORGA": "Chemicals", "ATUL": "Chemicals", "CLEAN": "Chemicals",
    "GHCL": "Chemicals", "FINEORG": "Chemicals", "GNFC": "Chemicals",

    # Textile / Apparel
    "PAGEIND": "Textile", "WELSPUNLIV": "Textile", "TRIDENT": "Textile",
    "VARDHMNGRP": "Textile", "RAYMOND": "Textile", "ARVIND": "Textile",
    "SUNFLAG": "Textile",

    # Diversified / Other
    "DABUR": "FMCG",
}


def get_sector(symbol: str) -> str:
    """Return the sector for a given NSE symbol, or empty string if unknown."""
    return SECTOR_MAPPING.get(symbol.upper(), "")
