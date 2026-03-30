#!/usr/bin/env python3
"""Test driver payment form parsing."""
import sys
sys.path.insert(0, "/data/.openclaw/workspace/investment-tracker")

test_text = """Company   1000944163 ONTARIO INC. Driver   Arcelao Kent Patrick Ba   From   08-Dec-2025   To   21-Dec-2025 Trip#   484170   Unit#   3369

Type   Date   City   Distance, PM   U/M
Pick-up Trailer   11-Dec-2025   CONCORD, ON   0.0
Drop-Off   15-Dec-2025   MESA, AZ   2151.6
Drop Trailer   15-Dec-2025   SANTA FE SPRINGS, CA   380.9
Pick-up Trailer   15-Dec-2025   SANTA FE SPRINGS, CA   0.0
Drop-Off   16-Dec-2025   CHATSWORTH, CA   42.0
Pick-Up   16-Dec-2025   VERNON, CA   33.3
Drop Trailer   21-Dec-2025   BRAMPTON, ON   2483.1
Route Via   21-Dec-2025   CONCORD, ON   15.8

Total Distance:   5106.7   miles
Total Hrs:   0.0

Item   Service   Qty   U/M   Rate   Per   Amount   Currency
1   H   0.0   0.0   $356.54
2   INSURANCE   1.0   -50.0   $-50.00
3   SAFETY BONUS   5106.7   0.02   $102.13
4   DRIVER PAY   5106.7   0.52   $2,655.48
5   EXTRA DROP   1.0   35.0   $35.00

Total Trip: $3,099.15   CAD
Total Miles:   5107
Total Hrs:   0.0
Total Service:   $3,099.15"""

# Test the parser directly (bypasses MIME type detection in _parse_pay_stub)
from app import _parse_driver_payment_form

result = _parse_driver_payment_form(test_text)
print("Result:", result)

assert result['employer'] == '1000944163 ONTARIO INC.', f"Employer mismatch: {result['employer']}"
assert result['pay_date'] == '2025-12-21', f"Date mismatch: {result['pay_date']}"
assert result['gross_income'] == 3149.15, f"Gross mismatch: {result['gross_income']}"
assert result['net_income'] == 3099.15, f"Net mismatch: {result['net_income']}"

print("ALL TESTS PASSED")
