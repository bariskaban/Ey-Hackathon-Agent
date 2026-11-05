-- Synthetic company records. All amounts are in EUR.

CREATE TABLE vendors (
    id               TEXT PRIMARY KEY,          -- V001
    name             TEXT NOT NULL,
    country          TEXT NOT NULL,             -- ISO 3166 alpha-2
    vat_number       TEXT NOT NULL,
    kvk_number       TEXT,                      -- Dutch Chamber of Commerce number, NL vendors only
    address          TEXT NOT NULL,
    iban             TEXT NOT NULL,
    iban_changed_at  TEXT,                      -- ISO date of the last change, NULL if never
    status           TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE purchase_orders (
    po_number        TEXT PRIMARY KEY,
    vendor_id        TEXT NOT NULL REFERENCES vendors(id),
    date             TEXT NOT NULL,
    total_excl_vat   REAL NOT NULL,
    status           TEXT NOT NULL              -- open / closed
);

CREATE TABLE po_lines (
    po_number        TEXT NOT NULL REFERENCES purchase_orders(po_number),
    line_no          INTEGER NOT NULL,
    sku              TEXT NOT NULL,
    description      TEXT NOT NULL,
    qty              REAL NOT NULL,
    unit_price       REAL NOT NULL,
    vat_category     TEXT NOT NULL,
    PRIMARY KEY (po_number, line_no)
);

CREATE TABLE goods_receipts (
    grn_id           TEXT PRIMARY KEY,
    po_number        TEXT NOT NULL REFERENCES purchase_orders(po_number),
    date             TEXT NOT NULL,
    sku              TEXT NOT NULL,
    qty_received     REAL NOT NULL
);

CREATE TABLE transactions (
    txn_id           TEXT PRIMARY KEY,
    vendor_id        TEXT NOT NULL REFERENCES vendors(id),
    date             TEXT NOT NULL,
    amount_eur       REAL NOT NULL,             -- amount paid, including VAT
    reference        TEXT,                      -- PO number, or a free-text reference
    invoice_no       TEXT,
    iban             TEXT,                      -- account the money went to
    status           TEXT NOT NULL              -- paid
);

CREATE TABLE invoices_seen (
    vendor_id             TEXT NOT NULL REFERENCES vendors(id),
    invoice_no            TEXT NOT NULL,
    normalized_invoice_no TEXT NOT NULL,        -- upper case, letters and digits only
    amount                REAL NOT NULL,        -- total including VAT
    date                  TEXT NOT NULL,
    status                TEXT NOT NULL         -- approved / paid
);

CREATE TABLE employees (                        -- only used for overlap checks, never returned
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    address          TEXT NOT NULL,
    iban             TEXT NOT NULL
);

-- Reference data
CREATE TABLE vat_rates (
    category         TEXT NOT NULL,
    rate             REAL NOT NULL,             -- percent
    valid_from       TEXT NOT NULL,
    valid_to         TEXT                       -- NULL = still valid
);

CREATE TABLE eu_countries (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    vat_prefix       TEXT NOT NULL
);

CREATE TABLE bank_codes (
    code             TEXT NOT NULL,
    country          TEXT NOT NULL,
    bank_name        TEXT NOT NULL,
    PRIMARY KEY (code, country)
);

CREATE TABLE sanctions (
    list             TEXT NOT NULL,
    name             TEXT NOT NULL,
    aliases          TEXT,                      -- semicolon separated
    country          TEXT,
    listed_on        TEXT
);

CREATE TABLE review_cases (                     -- append-only: evidence is never changed or deleted
    case_id            TEXT PRIMARY KEY,        -- RC-0001
    invoice_id         TEXT NOT NULL,
    verdict            TEXT NOT NULL,
    evidence_json      TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open',   -- only a person changes this, in the UI
    status_changed_by  TEXT,
    status_changed_at  TEXT,
    status_reason      TEXT
);

CREATE TABLE case_notes (                       -- append-only
    note_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id            TEXT NOT NULL REFERENCES review_cases(case_id),
    ts                 TEXT NOT NULL,
    author             TEXT NOT NULL,           -- 'agent' or a person's name
    text               TEXT NOT NULL
);

CREATE TRIGGER review_cases_no_delete BEFORE DELETE ON review_cases
BEGIN SELECT RAISE(ABORT, 'review cases are append-only'); END;

CREATE TRIGGER review_cases_evidence_immutable
BEFORE UPDATE OF case_id, invoice_id, verdict, evidence_json, created_at ON review_cases
BEGIN SELECT RAISE(ABORT, 'review case evidence cannot be changed'); END;

CREATE TRIGGER case_notes_no_update BEFORE UPDATE ON case_notes
BEGIN SELECT RAISE(ABORT, 'case notes are append-only'); END;

CREATE TRIGGER case_notes_no_delete BEFORE DELETE ON case_notes
BEGIN SELECT RAISE(ABORT, 'case notes are append-only'); END;

CREATE INDEX idx_txn_vendor_date ON transactions(vendor_id, date);
CREATE INDEX idx_seen_vendor ON invoices_seen(vendor_id, normalized_invoice_no);
CREATE INDEX idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX idx_cases_invoice ON review_cases(invoice_id, status);
CREATE INDEX idx_notes_case ON case_notes(case_id);
