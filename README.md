# Haldiram food ordering app

Haldiram food ordering app on the order-desk base: menu of Haldiram items with descriptions, cart, checkout with delivery / dine-in / pickup, UPI or COD payment, My Orders with live status, and an owner dashboard with a menu editor and order queue. Members keep name, phone, email and hashed password.

Built by its owner on [DoneJi](https://doneji.app) — the AI drafted it, the owner read every file and passed an explain-it-back exam on the code before it could ship.

## Run it locally

1. Set DATABASE_URL and SECRET_KEY in the environment.
2. Run the app; schema.sql creates tables and seeds the Haldiram menu.
3. Register the first account and tick 'I run the counter' to become the owner.

## Deployment

- **App**: Vercel, auto-deploys on every push to `main`
- **Database**: Supabase; the schema lives in `supabase/migrations/` and applies automatically on push
