import { Pool } from "pg";

export const createPool = (connectionString: string) =>
  new Pool({
    connectionString,
    max: Number(process.env.DATABASE_POOL_MAX ?? 10),
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
  repository_local_path text,
  repository_remote_url text,
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
  branch_name text,
  github_issue_url text,
  github_issue_number integer,
  repository_local_path text,
  repository_remote_url text,
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
create index if not exists board_cards_idea_idx on board_cards(idea_id);
create index if not exists ideas_project_status_updated_idx on ideas(project_id, status, updated_at desc);
create index if not exists board_cards_project_column_updated_idx on board_cards(project_id, column_name, updated_at desc);
create index if not exists ideas_project_updated_id_idx on ideas(project_id, updated_at desc, id desc);
create index if not exists board_cards_project_updated_id_idx on board_cards(project_id, updated_at desc, id desc);

alter table board_cards add column if not exists branch_name text;
alter table ideas add column if not exists repository_local_path text;
alter table ideas add column if not exists repository_remote_url text;
alter table board_cards add column if not exists repository_local_path text;
alter table board_cards add column if not exists repository_remote_url text;
alter table board_cards add column if not exists readiness_score integer;
alter table board_cards add column if not exists readiness_reason text;
alter table board_cards add column if not exists readiness_evaluated_at timestamptz;
alter table ideas add column if not exists readiness_score integer;
alter table ideas add column if not exists readiness_reason text;
alter table ideas add column if not exists readiness_evaluated_at timestamptz;
alter table ideas add column if not exists assignee text;
alter table board_cards add column if not exists assignee text;

create index if not exists ideas_project_assignee_idx on ideas(project_id, assignee);
create index if not exists board_cards_project_assignee_idx on board_cards(project_id, assignee);

create table if not exists task_dependencies (
  idea_id text not null references ideas(id) on delete cascade,
  depends_on_idea_id text not null references ideas(id) on delete cascade,
  created_at timestamptz not null,
  primary key (idea_id, depends_on_idea_id),
  check (idea_id <> depends_on_idea_id)
);
create index if not exists task_dependencies_depends_on_idx
  on task_dependencies(depends_on_idea_id);

create table if not exists idea_readiness_events (
  id text primary key,
  idea_id text not null references ideas(id) on delete cascade,
  score integer not null,
  reason text not null,
  created_at timestamptz not null
);
create index if not exists idea_readiness_events_idea_idx
  on idea_readiness_events(idea_id, created_at desc);

insert into idea_readiness_events (id, idea_id, score, reason, created_at)
select md5(random()::text || id), id, readiness_score,
       coalesce(readiness_reason, ''), coalesce(readiness_evaluated_at, now())
from ideas
where readiness_score is not null
  and not exists (select 1 from idea_readiness_events e where e.idea_id = ideas.id);
`;

export const initializeDatabase = async (pool: Pick<Pool, "query">) => {
  await pool.query(schemaSql);
};
