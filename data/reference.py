"""Reference data: VAT rates, EU countries, bank codes and a synthetic sanctions sample.

The sanctions entries are FICTIONAL. They stand in for the EU consolidated list
so the demo never depends on (or misrepresents) a real list.
"""

# (category, rate in percent, valid_from, valid_to)
VAT_RATES = [
    ("standard", 21.0, "2012-10-01", None),
    ("reduced", 6.0, "2012-10-01", "2018-12-31"),
    ("reduced", 9.0, "2019-01-01", None),
    ("zero", 0.0, "2000-01-01", None),
]

# Categories used on PO lines and invoices, mapped to the rate band above.
CATEGORY_TO_BAND = {
    "standard": "standard",
    "reduced_food": "reduced",
    "reduced_books": "reduced",
    "reduced_medicine": "reduced",
    "zero": "zero",
}

# (code, name, vat_prefix)
EU_COUNTRIES = [
    ("AT", "Austria", "AT"), ("BE", "Belgium", "BE"), ("BG", "Bulgaria", "BG"),
    ("HR", "Croatia", "HR"), ("CY", "Cyprus", "CY"), ("CZ", "Czechia", "CZ"),
    ("DK", "Denmark", "DK"), ("EE", "Estonia", "EE"), ("FI", "Finland", "FI"),
    ("FR", "France", "FR"), ("DE", "Germany", "DE"), ("GR", "Greece", "EL"),
    ("HU", "Hungary", "HU"), ("IE", "Ireland", "IE"), ("IT", "Italy", "IT"),
    ("LV", "Latvia", "LV"), ("LT", "Lithuania", "LT"), ("LU", "Luxembourg", "LU"),
    ("MT", "Malta", "MT"), ("NL", "Netherlands", "NL"), ("PL", "Poland", "PL"),
    ("PT", "Portugal", "PT"), ("RO", "Romania", "RO"), ("SK", "Slovakia", "SK"),
    ("SI", "Slovenia", "SI"), ("ES", "Spain", "ES"), ("SE", "Sweden", "SE"),
]

# (code, country, bank_name). Dutch banks use the 4-letter code in the IBAN.
BANK_CODES = [
    ("INGB", "NL", "ING Bank"),
    ("RABO", "NL", "Rabobank"),
    ("ABNA", "NL", "ABN AMRO"),
    ("TRIO", "NL", "Triodos Bank"),
    ("SNSB", "NL", "de Volksbank (SNS)"),
    ("KNAB", "NL", "Knab"),
    ("BUNQ", "NL", "bunq"),
    ("410", "BE", "KBC Bank"),
    ("001", "BE", "BNP Paribas Fortis"),
    ("310", "BE", "ING Belgium"),
    ("10010010", "DE", "Deutsche Bank"),
    ("37040044", "DE", "Commerzbank"),
    ("50010517", "DE", "ING-DiBa"),
]

# (list, name, aliases, country, listed_on) -- fictional sample entries
SANCTIONS = [
    ("EU-SAMPLE", "Aurora Petrochem Trading Ltd", "Aurora Petrochem;APT Ltd", "RU", "2022-06-03"),
    ("EU-SAMPLE", "Northern Star Maritime Holdings", "NS Maritime", "RU", "2022-03-15"),
    ("EU-SAMPLE", "Kaspar Volkov Logistics", "K. Volkov Logistics", "BY", "2022-12-16"),
    ("EU-SAMPLE", "Baltic Meridian Shipping Co", "Meridian Shipping", "RU", "2023-02-25"),
    ("EU-SAMPLE", "Tigris Commodities FZE", "Tigris FZE", "AE", "2023-08-04"),
    ("EU-SAMPLE", "Sevastopol Marine Services", "SMS Crimea", "UA", "2014-12-18"),
    ("EU-SAMPLE", "Orion Defence Components JSC", "Orion Defense Components", "RU", "2022-04-08"),
    ("EU-SAMPLE", "Red Delta Aviation Services", "RDA Services", "RU", "2022-06-03"),
    ("EU-SAMPLE", "Volga Steel Export", "Volga Steel Exports LLC", "RU", "2022-03-15"),
    ("EU-SAMPLE", "Zarya Industrial Group", "Zarya Group", "BY", "2022-12-16"),
    ("EU-SAMPLE", "Black Sea Bulk Carriers", "BS Bulk", "RU", "2023-02-25"),
    ("EU-SAMPLE", "Hadid Trading Partners", "Hadid Partners LLC", "SY", "2012-09-14"),
    ("EU-SAMPLE", "Ironbridge Capital Advisory", "Ironbridge Advisory", "RU", "2022-07-21"),
    ("EU-SAMPLE", "Silk Route Electronics LLC", "Silk Route Electronics", "IR", "2023-05-20"),
    ("EU-SAMPLE", "Ural Heavy Machinery Works", "Ural Machinery", "RU", "2022-03-15"),
    ("NL-SAMPLE", "Stichting Noordzee Relief", "Noordzee Relief Foundation", "NL", "2021-11-09"),
    ("NL-SAMPLE", "Rhein Delta Holding BV", "Rhine Delta Holding", "NL", "2020-05-18"),
    ("NL-SAMPLE", "Veluwe Vastgoed Partners", "Veluwe Property Partners", "NL", "2021-02-02"),
]
