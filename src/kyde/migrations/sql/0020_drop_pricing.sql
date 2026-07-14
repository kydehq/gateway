-- Cost reporting retired. The dashboard now reports token usage only, so the
-- pricing rate table and the USD→EUR FX setting that fed cost calculations are
-- no longer read by any code path. Drop both. Forward-only: there is no need to
-- preserve historical rates because nothing computes cost from them anymore.
DROP TABLE IF EXISTS pricing;
DELETE FROM settings WHERE key = 'FX_USD_EUR';
