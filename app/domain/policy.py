# app/domain/policy.py
#
# Numbers from docs/policy.md (ops wiki) and the note from Finance. These are
# the only places money rules live; the model never sees or decides them.

BASE_FARE = 25
FREE_DISTANCE_KM = 2
PER_KM_RATE = 6

DAILY_INCENTIVE_THRESHOLD = 12
DAILY_INCENTIVE_AMOUNT = 150

RIDER_CANCELLATION_PENALTY = 10

# "We only look at disputes for the last 7 days."
DISPUTE_WINDOW_DAYS = 7

# Finance: auto-pay up to ₹200 per dispute, once per rider per day.
AUTO_PAY_LIMIT = 200
AUTO_PAYS_PER_RIDER_PER_DAY = 1

# PaySwift accepts whole rupees from 1 to 10000 per payout.
PAYSWIFT_MAX_AMOUNT = 10000
