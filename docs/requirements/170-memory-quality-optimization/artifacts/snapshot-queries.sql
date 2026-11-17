-- Read-only queries used on the frozen private SQLite snapshot.
PRAGMA query_only=ON;
BEGIN;

-- semantic_status
select case when deleted_at is not null then 'deleted' when valid_until is not null then 'superseded' else 'active' end status,count(*) n from semantic group by status;

-- episodic_status
select case when deleted_at is not null then 'deleted' else 'active' end status,count(*) n from episodic group by status;

-- exact_semantic_duplicates
with d as(select count(*) n from semantic where deleted_at is null and valid_until is null group by statement having count(*)>1)select count(*) groups,sum(n) rows,sum(n-1) extra_rows,max(n) largest_group from d;

-- exact_episode_duplicates
with d as(select count(*) n from episodic where deleted_at is null group by summary having count(*)>1)select count(*) groups,sum(n) rows,sum(n-1) extra_rows,max(n) largest_group from d;

-- episodic_length
select count(*) total,sum(length(summary)>80) over_80,max(length(summary)) maximum,round(avg(length(summary)),1) mean from episodic where deleted_at is null;

-- speaker_origin
select speaker_origin,count(*) n from semantic where deleted_at is null and valid_until is null group by speaker_origin;

-- provenance_empty
select case when deleted_at is not null then 'deleted' when valid_until is not null then 'superseded' else 'active' end status,count(*) n from semantic where json_array_length(provenance)=0 group by status;

-- extraction_contract_versions
select output_contract_version,count(*) n,min(committed_at) first_at,max(committed_at) last_at from extraction_commit group by output_contract_version;

-- pending_state
select state,error_code,blocked_at,count(*) n from extraction_pending group by state,error_code,blocked_at;

COMMIT;
