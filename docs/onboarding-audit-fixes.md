# Onboarding audit fixes

## What was fixed

1. **Google OAuth onboarding drip**
   - Verified new Google OAuth signups now call `schedule_onboarding_emails(email)` only when `get_user(email) is None` before account creation.
   - Returning Google users do not re-enter the onboarding drip.

2. **Free lookup UI copy**
   - Updated the homepage, account page, and all trade landing pages to use the permanent-limit wording: `3 free lookups — no reset`.
   - This replaced the old reset-oriented counter copy in the visible free-lookup UI.

3. **One-time free limit email**
   - The permit lookup endpoint sends a Resend email when a logged-in user hits `403 free_limit_reached`.
   - Delivery is now guarded by `users.free_limit_email_sent` plus `users.free_limit_notice_sent_at`, so each user only gets the notice once.
   - Guests are skipped because the email send only runs when a valid session resolves to a user email.

4. **Schema drift**
   - Audited the SQLite schema in `data/cache.db` against `api/server.py` expectations.
   - Drift found: `users.free_limit_notice_sent_at` existed as a timestamp, but the code needed an explicit one-time guard column too. Added `users.free_limit_email_sent` safely with `ALTER TABLE`.
   - Safe fix path is implemented in `init_db()` via `ensure_table_columns(...)`, which adds missing columns with `ALTER TABLE` instead of recreating tables.
   - Current `data/cache.db` now includes both `users.free_limit_notice_sent_at` and `users.free_limit_email_sent`, plus the expected onboarding tables.
