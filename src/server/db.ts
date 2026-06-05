import { Pool } from "pg";

export const createPool = (connectionString: string) =>
  new Pool({
    connectionString,
    max: Number(process.env.DATABASE_POOL_MAX ?? 10)
  });

const schemaSql = `
create table if not exists projects (
  id text primary key,
  key text not null unique,
  title text not null,
  summary text not null default '',
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists ideas (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  task_number integer not null,
  title text not null,
  summary text not null default '',
  details text not null default '',
  status text not null,
  labels jsonb not null default '[]'::jsonb,
  acceptance_criteria jsonb not null default '[]'::jsonb,
  github_issue_url text,
  github_issue_number integer,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique(project_id, task_number)
);

create table if not exists board_cards (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  idea_id text references ideas(id) on delete set null,
  title text not null,
  details text not null default '',
  column_name text not null,
  github_issue_url text,
  github_issue_number integer,
  labels jsonb not null default '[]'::jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists documents (
  id text primary key,
  project_id text not null references projects(id) on delete cascade,
  title text not null,
  kind text not null,
  content text not null default '',
  linked_idea_ids jsonb not null default '[]'::jsonb,
  linked_card_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null
);

create table if not exists activity_events (
  id text primary key,
  type text not null,
  message text not null,
  created_at timestamptz not null
);

create table if not exists api_tokens (
  id text primary key,
  name text not null,
  token_hash text not null unique,
  created_at timestamptz not null,
  last_used_at timestamptz,
  revoked_at timestamptz
);

create table if not exists api_token_projects (
  token_id text not null references api_tokens(id) on delete cascade,
  project_id text not null references projects(id) on delete cascade,
  primary key (token_id, project_id)
);

create index if not exists ideas_project_updated_idx on ideas(project_id, updated_at desc);
create index if not exists board_cards_project_updated_idx on board_cards(project_id, updated_at desc);
create index if not exists documents_project_updated_idx on documents(project_id, updated_at desc);
create index if not exists activity_events_created_idx on activity_events(created_at desc);
`;

export const initializeDatabase = async (pool: Pick<Pool, "query">) => {
  await pool.query(schemaSql);
};
