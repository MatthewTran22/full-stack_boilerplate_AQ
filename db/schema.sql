create table if not exists clones (
  id uuid primary key default gen_random_uuid(),
  url text not null,
  generated_html text not null,
  screenshot_url text,
  sandbox_url text,
  created_at timestamptz default now()
);
