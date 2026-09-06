-- Initial schema for Haldiram food ordering app.
-- DoneJi ran this file against your database when it made it, and
-- recorded it in supabase_migrations.schema_migrations — the same
-- record Supabase's own GitHub integration keeps, so linking the repo
-- later applies only NEWER files. To change the schema, add a NEW file
-- with a newer timestamp — never edit this one: applied migrations are
-- tracked by timestamp and an edited file is silently skipped.

-- Four tables, because an order is not one thing: it is who ordered (a
-- Haldiram member), what is on the menu, the order itself, and the LINES
-- inside it.

create table if not exists users (
  id            serial primary key,
  name          text not null,
  -- The spec keeps a phone number on the member record. It is not UNIQUE on
  -- purpose: a family can share one number; it is the email that must differ.
  phone         text not null constraint users_phone_check check (length(phone) between 10 and 15),
  email         text not null unique,
  password_hash text not null,
  -- 'owner' manages items and the queue; 'customer' browses and orders.
  role          text not null default 'customer' constraint users_role_check check (role in ('owner', 'customer'))
);

create table if not exists items (
  id           serial primary key,
  name         text          not null unique,
  description  text          not null default '',
  category     text          not null,          -- e.g. 'Namkeen', 'Sweets', 'Thali'
  price        numeric(7,2)  not null constraint items_price_check check (price >= 0),
  is_available boolean       not null default true,
  -- A direct https image link the owner pastes in the menu editor. NULL means
  -- the menu shows a neutral placeholder photo instead.
  image_url    text
);

create table if not exists orders (
  id         serial primary key,
  user_id    integer not null references users(id) on delete cascade,
  status     text    not null default 'placed'
             constraint orders_status_check check (status in ('placed', 'preparing', 'ready')),
  -- HOW the customer wants their Haldiram food: home delivery, counter pickup,
  -- or dine-in at a table. The three detail columns are one-per-mode and
  -- nullable; the one matching the chosen mode is required BY PYTHON in
  -- place_order — a cross-column CHECK here is not allowed, so the database
  -- keeps single-column, named constraints only.
  fulfillment text   not null default 'pickup'
             constraint orders_fulfillment_check check (fulfillment in ('delivery', 'pickup', 'table')),
  address     text,
  pickup_time text,  -- text on purpose: "6:30 pm" broke a timestamp column once
  table_no    integer constraint orders_table_no_check check (table_no > 0),
  -- HOW the customer chose to pay: UPI now, or cash on delivery / at the
  -- counter. Whether money actually arrived lives in payments — the choice
  -- and the fact are two different things.
  pay_method text    constraint orders_pay_method_check check (pay_method in ('upi', 'counter')),
  placed_at  timestamp not null default now()
);

create table if not exists order_items (
  id             serial primary key,
  order_id       integer      not null references orders(id) on delete cascade,
  item_id        integer      not null references items(id) on delete cascade,
  qty            integer      not null constraint order_items_qty_check check (qty > 0),
  -- The price is COPIED here on purpose. The menu price changes tomorrow;
  -- the bill for an order already placed must never change with it.
  price_at_order numeric(7,2) not null
);

-- The opening Haldiram menu: namkeen, sweets, thalis, snacks and drinks.
-- ON CONFLICT makes this safe to run on every start — an existing name is
-- simply left alone. This is REPRESENTATION data; a real Haldiram branch
-- replaces it from the owner's menu editor. image_url stays NULL so the
-- platform's verified dish photos (photos.py) serve where they can.
insert into items (name, description, category, price) values
  ('Aloo Bhujia',        'Crisp potato noodle namkeen, the classic Haldiram pack', 'Namkeen', 50),
  ('Moong Dal Namkeen',  'Light, salted fried moong dal',                          'Namkeen', 45),
  ('Navratan Mixture',   'A rich mix of sev, nuts and lentils',                    'Namkeen', 65),
  ('Rasgulla',           'Soft chhena balls in light sugar syrup (2 pc)',          'Sweets',  60),
  ('Gulab Jamun',        'Warm khoya dumplings in syrup (2 pc)',                   'Sweets',  70),
  ('Kaju Katli',         'Cashew fudge, 250 g box',                                'Sweets', 250),
  ('Samosa',             'Flaky pastry with spiced potato filling',                'Snacks',  25),
  ('Raj Kachori',        'Giant crisp kachori with chutneys and curd',             'Snacks',  60),
  ('Special Thali',      'Two sabzis, dal, rice, roti and sweet',                  'Thali',   150),
  ('Paneer Tikka Thali', 'Paneer tikka, dal makhani, rice, roti and salad',        'Thali',   180),
  ('Masala Dosa',        'Crisp dosa with potato filling and sambar',              'South Indian', 90),
  ('Masala Chai',        'Hot spiced tea, cut only',                               'Drinks',  20),
  ('Badam Milk',         'Chilled almond milkshake',                               'Drinks',  40)
on conflict (name) do nothing;

-- One payment per thing being paid for. The columns split deliberately down
-- the middle: what the CUSTOMER says happened (a 12-digit UPI reference), and
-- what the OWNER confirmed after looking at their own bank app. Never collapse
-- them into one "paid" flag — a claim is not a confirmation.
create table if not exists payments (
  id          serial primary key,
  -- Which row is being paid for: 'order' in this app.
  target_kind text not null,
  target_id   integer not null,
  amount      numeric(10,2) not null constraint payments_amount_check check (amount > 0),
  status      text not null default 'awaiting'
              constraint payments_status_check check (status in ('awaiting', 'claimed', 'verified', 'rejected')),

  -- What the customer typed. Never trusted: a UPI reference is 12 digits and
  -- anyone can type 12 digits. The CHECK is shape, not proof.
  claimed_ref text constraint payments_claimed_ref_check check (claimed_ref ~ '^[0-9]{12}$'),
  claimed_by  integer references users(id) on delete set null,
  claimed_at  timestamp,

  -- What the owner confirmed, by eye, against their own UPI app.
  verified_by integer references users(id) on delete set null,
  verified_at timestamp,

  note        text not null default '',
  -- One payment row per order. Makes the insert idempotent, so opening the
  -- pay panel twice cannot create two rows.
  unique (target_kind, target_id)
);
