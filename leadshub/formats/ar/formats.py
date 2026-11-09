# Arabic UI shows amounts as 250,000.00 (same separators as English), the
# common convention for EGP/USD figures in Egypt. Overrides Django's defaults.
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
