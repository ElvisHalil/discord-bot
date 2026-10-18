"""
Static aviation catalogs, airport directories, gateway mappings, airline codes,
and stopword sets for the flights skill package.
"""

from src.skills.flights.models import (
    AirportRecord,
    GatewayAirportRecord,
    NearbyHubRecord,
)


def _build_airport_directory() -> dict[str, AirportRecord]:
    """Builds the canonical alias -> AirportRecord lookup table from deduplicated airport definitions."""
    catalog: tuple[tuple[AirportRecord, tuple[str, ...]], ...] = (
        # Canada
        (AirportRecord("YYC", "Calgary International Airport", "Calgary", "Canada"), ("yyc", "calgary")),
        (AirportRecord("YEG", "Edmonton International Airport", "Edmonton", "Canada"), ("yeg", "edmonton")),
        (AirportRecord("YVR", "Vancouver International Airport", "Vancouver", "Canada"), ("yvr", "vancouver")),
        (AirportRecord("YYZ", "Toronto Pearson International Airport", "Toronto", "Canada"), ("yyz", "toronto")),
        (AirportRecord("YTZ", "Billy Bishop Toronto City Airport", "Toronto", "Canada"), ("ytz",)),
        (AirportRecord("YUL", "Montréal-Pierre Elliott Trudeau International Airport", "Montreal", "Canada"), ("yul", "montreal")),
        (AirportRecord("YOW", "Ottawa Macdonald-Cartier International Airport", "Ottawa", "Canada"), ("yow", "ottawa")),
        (AirportRecord("YWG", "Winnipeg Richardson International Airport", "Winnipeg", "Canada"), ("ywg", "winnipeg")),
        (AirportRecord("YHZ", "Halifax Stanfield International Airport", "Halifax", "Canada"), ("yhz", "halifax")),
        (AirportRecord("YQB", "Québec City Jean Lesage International Airport", "Quebec City", "Canada"), ("yqb", "quebec", "quebec city")),
        (AirportRecord("YYJ", "Victoria International Airport", "Victoria", "Canada"), ("yyj", "victoria")),
        (AirportRecord("YLW", "Kelowna International Airport", "Kelowna", "Canada"), ("ylw", "kelowna")),
        (AirportRecord("YXE", "Saskatoon John G. Diefenbaker International Airport", "Saskatoon", "Canada"), ("yxe", "saskatoon")),
        (AirportRecord("YQR", "Regina International Airport", "Regina", "Canada"), ("yqr", "regina")),
        (AirportRecord("YYT", "St. John's International Airport", "St. John's", "Canada"), ("yyt",)),
        # United States
        (AirportRecord("JFK", "John F. Kennedy International Airport", "New York", "USA"), ("jfk", "new york", "nyc")),
        (AirportRecord("LGA", "LaGuardia Airport", "New York", "USA"), ("lga",)),
        (AirportRecord("EWR", "Newark Liberty International Airport", "Newark", "USA"), ("ewr", "newark")),
        (AirportRecord("LAX", "Los Angeles International Airport", "Los Angeles", "USA"), ("lax", "los angeles", "la")),
        (AirportRecord("SFO", "San Francisco International Airport", "San Francisco", "USA"), ("sfo", "san francisco")),
        (AirportRecord("ORD", "Chicago O'Hare International Airport", "Chicago", "USA"), ("ord", "chicago")),
        (AirportRecord("MDW", "Chicago Midway International Airport", "Chicago", "USA"), ("mdw",)),
        (AirportRecord("MIA", "Miami International Airport", "Miami", "USA"), ("mia", "miami")),
        (AirportRecord("FLL", "Fort Lauderdale-Hollywood International Airport", "Fort Lauderdale", "USA"), ("fll", "fort lauderdale")),
        (AirportRecord("MCO", "Orlando International Airport", "Orlando", "USA"), ("mco", "orlando")),
        (AirportRecord("LAS", "Harry Reid International Airport", "Las Vegas", "USA"), ("las", "las vegas", "vegas")),
        (AirportRecord("SEA", "Seattle-Tacoma International Airport", "Seattle", "USA"), ("sea", "seattle")),
        (AirportRecord("DFW", "Dallas/Fort Worth International Airport", "Dallas", "USA"), ("dfw", "dallas")),
        (AirportRecord("IAH", "George Bush Intercontinental Airport", "Houston", "USA"), ("iah", "houston")),
        (AirportRecord("ATL", "Hartsfield-Jackson Atlanta International Airport", "Atlanta", "USA"), ("atl", "atlanta")),
        (AirportRecord("DEN", "Denver International Airport", "Denver", "USA"), ("den", "denver")),
        (AirportRecord("BOS", "Boston Logan International Airport", "Boston", "USA"), ("bos", "boston")),
        (AirportRecord("IAD", "Washington Dulles International Airport", "Washington D.C.", "USA"), ("iad", "washington")),
        (AirportRecord("DCA", "Ronald Reagan Washington National Airport", "Washington D.C.", "USA"), ("dca",)),
        (AirportRecord("PHX", "Phoenix Sky Harbor International Airport", "Phoenix", "USA"), ("phx", "phoenix")),
        (AirportRecord("SAN", "San Diego International Airport", "San Diego", "USA"), ("san", "san diego")),
        (AirportRecord("MSP", "Minneapolis-Saint Paul International Airport", "Minneapolis", "USA"), ("msp", "minneapolis")),
        (AirportRecord("DTW", "Detroit Metropolitan Wayne County Airport", "Detroit", "USA"), ("dtw", "detroit")),
        (AirportRecord("PHL", "Philadelphia International Airport", "Philadelphia", "USA"), ("phl", "philadelphia")),
        (AirportRecord("CLT", "Charlotte Douglas International Airport", "Charlotte", "USA"), ("clt", "charlotte")),
        (AirportRecord("SLC", "Salt Lake City International Airport", "Salt Lake City", "USA"), ("slc", "salt lake city")),
        (AirportRecord("HNL", "Daniel K. Inouye International Airport", "Honolulu", "USA"), ("hnl", "honolulu", "hawaii")),
        (AirportRecord("OGG", "Kahului Airport", "Maui", "USA"), ("ogg", "maui")),
        (AirportRecord("AUS", "Austin-Bergstrom International Airport", "Austin", "USA"), ("aus", "austin")),
        (AirportRecord("BNA", "Nashville International Airport", "Nashville", "USA"), ("bna", "nashville")),
        (AirportRecord("PDX", "Portland International Airport", "Portland", "USA"), ("pdx", "portland")),
        # Europe & Balkans
        (AirportRecord("LHR", "London Heathrow Airport", "London", "UK"), ("lhr", "london", "heathrow")),
        (AirportRecord("LGW", "London Gatwick Airport", "London", "UK"), ("lgw", "gatwick")),
        (AirportRecord("CDG", "Paris Charles de Gaulle Airport", "Paris", "France"), ("cdg", "paris")),
        (AirportRecord("ORY", "Paris Orly Airport", "Paris", "France"), ("ory",)),
        (AirportRecord("NCE", "Nice Côte d'Azur Airport", "Nice", "France"), ("nce", "nice")),
        (AirportRecord("LYS", "Lyon–Saint-Exupéry Airport", "Lyon", "France"), ("lys", "lyon")),
        (AirportRecord("FRA", "Frankfurt Airport", "Frankfurt", "Germany"), ("fra", "frankfurt")),
        (AirportRecord("MUC", "Munich Airport", "Munich", "Germany"), ("muc", "munich")),
        (AirportRecord("BER", "Berlin Brandenburg Airport", "Berlin", "Germany"), ("ber", "berlin")),
        (AirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam", "Netherlands"), ("ams", "amsterdam")),
        (AirportRecord("BRU", "Brussels Airport", "Brussels", "Belgium"), ("bru", "brussels")),
        (AirportRecord("MAD", "Adolfo Suárez Madrid–Barajas Airport", "Madrid", "Spain"), ("mad", "madrid")),
        (AirportRecord("BCN", "Josep Tarradellas Barcelona–El Prat Airport", "Barcelona", "Spain"), ("bcn", "barcelona")),
        (AirportRecord("AGP", "Málaga-Costa del Sol Airport", "Malaga", "Spain"), ("agp", "malaga")),
        (AirportRecord("PMI", "Palma de Mallorca Airport", "Palma", "Spain"), ("pmi",)),
        (AirportRecord("FCO", "Rome Fiumicino Airport", "Rome", "Italy"), ("fco", "rome")),
        (AirportRecord("MXP", "Milan Malpensa Airport", "Milan", "Italy"), ("mxp", "milan")),
        (AirportRecord("VCE", "Venice Marco Polo Airport", "Venice", "Italy"), ("vce", "venice")),
        (AirportRecord("NAP", "Naples International Airport", "Naples", "Italy"), ("nap", "naples")),
        (AirportRecord("ZRH", "Zurich Airport", "Zurich", "Switzerland"), ("zrh", "zurich")),
        (AirportRecord("GVA", "Geneva Airport", "Geneva", "Switzerland"), ("gva", "geneva")),
        (AirportRecord("VIE", "Vienna International Airport", "Vienna", "Austria"), ("vie", "vienna")),
        (AirportRecord("DUB", "Dublin Airport", "Dublin", "Ireland"), ("dub", "dublin")),
        (AirportRecord("LIS", "Humberto Delgado Airport", "Lisbon", "Portugal"), ("lis", "lisbon")),
        (AirportRecord("OPO", "Francisco Sá Carneiro Airport", "Porto", "Portugal"), ("opo", "porto")),
        (AirportRecord("ATH", "Athens International Airport", "Athens", "Greece"), ("ath", "athens")),
        (AirportRecord("IST", "Istanbul Airport", "Istanbul", "Turkey"), ("ist", "istanbul")),
        (AirportRecord("SAW", "Istanbul Sabiha Gökçen International Airport", "Istanbul", "Turkey"), ("saw",)),
        (AirportRecord("CPH", "Copenhagen Airport", "Copenhagen", "Denmark"), ("cph", "copenhagen")),
        (AirportRecord("ARN", "Stockholm Arlanda Airport", "Stockholm", "Sweden"), ("arn", "stockholm")),
        (AirportRecord("OSL", "Oslo Airport", "Oslo", "Norway"), ("osl", "oslo")),
        (AirportRecord("HEL", "Helsinki-Vantaa Airport", "Helsinki", "Finland"), ("hel", "helsinki")),
        (AirportRecord("WAW", "Warsaw Chopin Airport", "Warsaw", "Poland"), ("waw", "warsaw")),
        (AirportRecord("PRG", "Václav Havel Airport Prague", "Prague", "Czechia"), ("prg", "prague")),
        (AirportRecord("BUD", "Budapest Ferenc Liszt International Airport", "Budapest", "Hungary"), ("bud", "budapest")),
        (AirportRecord("ZAG", "Franjo Tuđman Airport Zagreb", "Zagreb", "Croatia"), ("zag", "zagreb")),
        (AirportRecord("SPU", "Split Airport", "Split", "Croatia"), ("spu", "split")),
        (AirportRecord("DBV", "Dubrovnik Airport", "Dubrovnik", "Croatia"), ("dbv", "dubrovnik")),
        (AirportRecord("ZAD", "Zadar Airport", "Zadar", "Croatia"), ("zad", "zadar")),
        (AirportRecord("PUY", "Pula Airport", "Pula", "Croatia"), ("puy", "pula")),
        (AirportRecord("SJJ", "Sarajevo International Airport", "Sarajevo", "Bosnia and Herzegovina"), ("sjj", "sarajevo")),
        (AirportRecord("TZL", "Tuzla International Airport", "Tuzla", "Bosnia and Herzegovina"), ("tzl", "tuzla")),
        (AirportRecord("BNX", "Banja Luka International Airport", "Banja Luka", "Bosnia and Herzegovina"), ("bnx", "banja luka")),
        (AirportRecord("OMO", "Mostar International Airport", "Mostar", "Bosnia and Herzegovina"), ("omo", "mostar")),
        (AirportRecord("BEG", "Belgrade Nikola Tesla Airport", "Belgrade", "Serbia"), ("beg", "belgrade", "beograd")),
        (AirportRecord("LJU", "Ljubljana Jože Pučnik Airport", "Ljubljana", "Slovenia"), ("lju", "ljubljana")),
        (AirportRecord("TGD", "Podgorica Airport", "Podgorica", "Montenegro"), ("tgd", "podgorica")),
        (AirportRecord("TIV", "Tivat Airport", "Tivat", "Montenegro"), ("tiv", "tivat")),
        (AirportRecord("SKP", "Skopje International Airport", "Skopje", "North Macedonia"), ("skp", "skopje")),
        (AirportRecord("TIA", "Tirana International Airport Nënë Tereza", "Tirana", "Albania"), ("tia", "tirana")),
        (AirportRecord("PRN", "Pristina International Airport", "Pristina", "Kosovo"), ("prn", "pristina")),
        (AirportRecord("OTP", "Bucharest Henri Coandă International Airport", "Bucharest", "Romania"), ("otp", "bucharest")),
        (AirportRecord("SOF", "Sofia Airport", "Sofia", "Bulgaria"), ("sof", "sofia")),
        (AirportRecord("DUS", "Düsseldorf Airport", "Düsseldorf", "Germany"), ("dus", "dusseldorf", "duesseldorf", "düsseldorf")),
        (AirportRecord("HAM", "Hamburg Airport", "Hamburg", "Germany"), ("ham", "hamburg")),
        (AirportRecord("STR", "Stuttgart Airport", "Stuttgart", "Germany"), ("str", "stuttgart")),
        (AirportRecord("CGN", "Cologne Bonn Airport", "Cologne", "Germany"), ("cgn", "cologne")),
        (AirportRecord("HAJ", "Hannover Airport", "Hannover", "Germany"), ("haj", "hannover")),
        (AirportRecord("NUE", "Nuremberg Airport", "Nuremberg", "Germany"), ("nue", "nuremberg")),
        (AirportRecord("KRK", "John Paul II International Airport Kraków-Balice", "Krakow", "Poland"), ("krk", "krakow")),
        (AirportRecord("EDI", "Edinburgh Airport", "Edinburgh", "UK"), ("edi", "edinburgh")),
        (AirportRecord("MAN", "Manchester Airport", "Manchester", "UK"), ("man", "manchester")),
        (AirportRecord("KEF", "Keflavík International Airport", "Reykjavik", "Iceland"), ("kef", "iceland", "reykjavik")),
        # Asia, Middle East & Oceania
        (AirportRecord("NRT", "Narita International Airport", "Tokyo", "Japan"), ("nrt",)),
        (AirportRecord("HND", "Tokyo Haneda Airport", "Tokyo", "Japan"), ("hnd", "tokyo")),
        (AirportRecord("KIX", "Kansai International Airport", "Osaka", "Japan"), ("kix", "osaka")),
        (AirportRecord("ICN", "Incheon International Airport", "Seoul", "South Korea"), ("icn", "seoul")),
        (AirportRecord("HKG", "Hong Kong International Airport", "Hong Kong", "Hong Kong"), ("hkg", "hong kong")),
        (AirportRecord("TPE", "Taiwan Taoyuan International Airport", "Taipei", "Taiwan"), ("tpe", "taipei")),
        (AirportRecord("SIN", "Singapore Changi Airport", "Singapore", "Singapore"), ("sin", "singapore")),
        (AirportRecord("BKK", "Suvarnabhumi Airport", "Bangkok", "Thailand"), ("bkk", "bangkok")),
        (AirportRecord("MNL", "Ninoy Aquino International Airport", "Manila", "Philippines"), ("mnl", "manila")),
        (AirportRecord("DEL", "Indira Gandhi International Airport", "New Delhi", "India"), ("del", "delhi", "new delhi")),
        (AirportRecord("BOM", "Chhatrapati Shivaji Maharaj International Airport", "Mumbai", "India"), ("bom", "mumbai")),
        (AirportRecord("DXB", "Dubai International Airport", "Dubai", "UAE"), ("dxb", "dubai")),
        (AirportRecord("AUH", "Zayed International Airport", "Abu Dhabi", "UAE"), ("auh", "abu dhabi")),
        (AirportRecord("DOH", "Hamad International Airport", "Doha", "Qatar"), ("doh", "doha")),
        (AirportRecord("SYD", "Sydney Kingsford Smith Airport", "Sydney", "Australia"), ("syd", "sydney")),
        (AirportRecord("MEL", "Melbourne Airport", "Melbourne", "Australia"), ("mel", "melbourne")),
        (AirportRecord("AKL", "Auckland Airport", "Auckland", "New Zealand"), ("akl", "auckland")),
        # Mexico, Caribbean & South America
        (AirportRecord("MEX", "Mexico City International Airport", "Mexico City", "Mexico"), ("mex", "mexico city")),
        (AirportRecord("CUN", "Cancún International Airport", "Cancun", "Mexico"), ("cun", "cancun")),
        (AirportRecord("PVR", "Licenciado Gustavo Díaz Ordaz International Airport", "Puerto Vallarta", "Mexico"), ("pvr", "puerto vallarta")),
        (AirportRecord("SJD", "Los Cabos International Airport", "Cabo San Lucas", "Mexico"), ("sjd", "cabo", "los cabos")),
        (AirportRecord("GRU", "São Paulo/Guarulhos International Airport", "Sao Paulo", "Brazil"), ("gru", "sao paulo")),
        (AirportRecord("GIG", "Rio de Janeiro/Galeão International Airport", "Rio de Janeiro", "Brazil"), ("gig", "rio")),
        (AirportRecord("BOG", "El Dorado International Airport", "Bogota", "Colombia"), ("bog", "bogota")),
        (AirportRecord("EZE", "Ministro Pistarini International Airport", "Buenos Aires", "Argentina"), ("eze", "buenos aires")),
    )
    directory: dict[str, AirportRecord] = {}
    for record, aliases in catalog:
        for alias in aliases:
            directory[alias] = record
    return directory


AIRPORT_DIRECTORY: dict[str, AirportRecord] = _build_airport_directory()

# Nearby major international hub alternatives when a secondary airport has no published long-range schedules
NEARBY_HUB_AIRPORTS: dict[str, NearbyHubRecord] = {
    "YEG": NearbyHubRecord("YYC", "Calgary"),
    "YYJ": NearbyHubRecord("YVR", "Vancouver"),
    "YLW": NearbyHubRecord("YVR", "Vancouver"),
    "YXE": NearbyHubRecord("YYC", "Calgary"),
    "YQR": NearbyHubRecord("YYC", "Calgary"),
    "YOW": NearbyHubRecord("YUL", "Montreal"),
    "YQB": NearbyHubRecord("YUL", "Montreal"),
    "YTZ": NearbyHubRecord("YYZ", "Toronto Pearson"),
    "DUS": NearbyHubRecord("FRA", "Frankfurt"),
    "CGN": NearbyHubRecord("FRA", "Frankfurt"),
    "STR": NearbyHubRecord("FRA", "Frankfurt"),
    "SJJ": NearbyHubRecord("ZAG", "Zagreb"),
    "TZL": NearbyHubRecord("BEG", "Belgrade"),
    "BNX": NearbyHubRecord("ZAG", "Zagreb"),
    "SPU": NearbyHubRecord("ZAG", "Zagreb"),
    "DBV": NearbyHubRecord("ZAG", "Zagreb"),
    "ZAD": NearbyHubRecord("ZAG", "Zagreb"),
    "LJU": NearbyHubRecord("ZAG", "Zagreb"),
}

# Primary nonstop international gateway hubs from major origins (for 1-stop schedule verification >6 months out)
ORIGIN_INTERNATIONAL_GATEWAYS: dict[str, list[GatewayAirportRecord]] = {
    "YEG": [
        GatewayAirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam"),
        GatewayAirportRecord("FRA", "Frankfurt Airport", "Frankfurt"),
    ],
    "YYC": [
        GatewayAirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam"),
        GatewayAirportRecord("LHR", "London Heathrow Airport", "London"),
        GatewayAirportRecord("FRA", "Frankfurt Airport", "Frankfurt"),
        GatewayAirportRecord("CDG", "Charles de Gaulle Airport", "Paris"),
        GatewayAirportRecord("FCO", "Leonardo da Vinci–Fiumicino Airport", "Rome"),
        GatewayAirportRecord("NRT", "Narita International Airport", "Tokyo"),
    ],
    "YVR": [
        GatewayAirportRecord("FRA", "Frankfurt Airport", "Frankfurt"),
        GatewayAirportRecord("LHR", "London Heathrow Airport", "London"),
        GatewayAirportRecord("CDG", "Charles de Gaulle Airport", "Paris"),
        GatewayAirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam"),
        GatewayAirportRecord("ZRH", "Zurich Airport", "Zurich"),
        GatewayAirportRecord("MUC", "Munich Airport", "Munich"),
        GatewayAirportRecord("HND", "Tokyo Haneda Airport", "Tokyo"),
    ],
    "YYZ": [
        GatewayAirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam"),
        GatewayAirportRecord("FRA", "Frankfurt Airport", "Frankfurt"),
        GatewayAirportRecord("LHR", "London Heathrow Airport", "London"),
        GatewayAirportRecord("CDG", "Charles de Gaulle Airport", "Paris"),
        GatewayAirportRecord("VIE", "Vienna International Airport", "Vienna"),
        GatewayAirportRecord("MUC", "Munich Airport", "Munich"),
        GatewayAirportRecord("ZRH", "Zurich Airport", "Zurich"),
    ],
    "YUL": [
        GatewayAirportRecord("CDG", "Charles de Gaulle Airport", "Paris"),
        GatewayAirportRecord("AMS", "Amsterdam Airport Schiphol", "Amsterdam"),
        GatewayAirportRecord("FRA", "Frankfurt Airport", "Frankfurt"),
        GatewayAirportRecord("LHR", "London Heathrow Airport", "London"),
        GatewayAirportRecord("VIE", "Vienna International Airport", "Vienna"),
        GatewayAirportRecord("ZRH", "Zurich Airport", "Zurich"),
    ],
}

# Known commercial airline IATA/ICAO prefixes for flight-number identification
AIRLINE_CODES: frozenset[str] = frozenset({
    "AC", "ACA", "WS", "WJA", "PD", "POE", "F8", "FLE", "TS", "TSC",
    "UA", "UAL", "AA", "AAL", "DL", "DAL", "WN", "SWA", "AS", "ASA", "B6", "JBU", "NK", "NKS", "F9", "FFT",
    "BA", "BAW", "VS", "VIR", "LH", "DLH", "AF", "AFR", "KL", "KLM", "IB", "IBE", "AZ", "ITY", "LX", "SWR",
    "OS", "AUA", "EI", "EIN", "SK", "SAS", "AY", "FIN", "TP", "TAP", "TK", "THY", "LO", "LOT", "OU", "CTN",
    "EK", "UAE", "QR", "QTR", "EY", "ETD", "SQ", "SIA", "CX", "CPA", "JL", "JAL", "NH", "ANA",
    "KE", "KAL", "OZ", "AAR", "BR", "EVA", "CI", "CAL", "QF", "QFA", "NZ", "ANZ", "AM", "AMX", "LA", "LAN",
})

MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

WEEKDAY_MAP: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

NON_LOCATION_STOPWORDS: frozenset[str] = frozenset(
    set(MONTH_MAP.keys())
    | set(WEEKDAY_MAP.keys())
    | {
        "trip", "planning", "plan", "year", "years", "month", "months", "week", "weeks",
        "day", "days", "today", "tomorrow", "tonight", "weekend", "next", "this", "during",
        "those", "these", "what", "when", "where", "which", "who", "how", "why", "best",
        "good", "price", "prices", "fare", "fares", "stop", "stops", "single", "only",
        "any", "all", "get", "find", "show", "give", "can", "you", "me", "my", "our",
        "the", "and", "for", "from", "to", "in", "on", "at", "by", "with", "about",
    }
)

NON_FLIGHT_NUMBER_PREFIXES: frozenset[str] = frozenset(
    NON_LOCATION_STOPWORDS
    | {
        "in", "to", "on", "at", "by", "of", "or", "bo", "cs", "f1", "pm", "am",
        "for", "max", "min", "top", "day", "way", "out", "via", "any", "all",
        "one", "two", "six", "per", "leg", "hub", "get", "see", "can", "you",
        "the", "and", "not", "are", "was", "has", "had", "but", "now", "new",
        "old", "low", "avg", "est", "cad", "usd", "eur", "gbp",
    }
)

