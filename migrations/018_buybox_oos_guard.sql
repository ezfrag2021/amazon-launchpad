-- Guard: if an ASIN is out of stock, do not treat it as an active buy-box suppression.
-- This keeps OOS as the primary classification and avoids false "Lost Buy Box" prioritization.

CREATE OR REPLACE FUNCTION public.apply_buybox_oos_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_out_of_stock BOOLEAN := FALSE;
BEGIN
    SELECT
        (COALESCE(fi.is_out_of_stock, FALSE) OR COALESCE(fi.afn_fulfillable_quantity, 0) <= 0)
    INTO v_is_out_of_stock
    FROM public.fba_inventory fi
    WHERE fi.asin = NEW.asin
    ORDER BY fi.report_date DESC NULLS LAST, fi.updated_at DESC NULLS LAST
    LIMIT 1;

    IF COALESCE(v_is_out_of_stock, FALSE) THEN
        NEW.is_active := FALSE;
        NEW.resolved_at := COALESCE(NEW.resolved_at, NEW.detected_at, NOW());
        NEW.resolution_reason := CASE
            WHEN NEW.resolution_reason IS NULL OR BTRIM(NEW.resolution_reason) = '' THEN 'OUT_OF_STOCK'
            ELSE NEW.resolution_reason
        END;
    END IF;

    NEW.updated_at := NOW();
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_buybox_oos_guard ON public.buybox_suppression_events;

CREATE TRIGGER trg_buybox_oos_guard
BEFORE INSERT OR UPDATE ON public.buybox_suppression_events
FOR EACH ROW
EXECUTE FUNCTION public.apply_buybox_oos_guard();
