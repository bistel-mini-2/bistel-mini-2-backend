-- Dodam Supabase bootstrap schema generated from SQLAlchemy models.
-- Apply before the existing non-purge db/migrations files.
BEGIN;

CREATE TABLE IF NOT EXISTS policy (
	policy_id SERIAL NOT NULL, 
	policy_code VARCHAR(100) NOT NULL, 
	policy_name VARCHAR(255) NOT NULL, 
	main_category VARCHAR(100), 
	sub_category VARCHAR(100), 
	provider_name VARCHAR(255), 
	provider_type VARCHAR(50), 
	region_scope VARCHAR(50), 
	region_code VARCHAR(50), 
	benefit_type VARCHAR(50), 
	application_status VARCHAR(50), 
	application_start_date DATE, 
	application_end_date DATE, 
	official_url TEXT, 
	contact VARCHAR(255), 
	last_verified_at TIMESTAMP WITHOUT TIME ZONE, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (policy_id), 
	UNIQUE (policy_code)
);
CREATE INDEX IF NOT EXISTS ix_policy_policy_name ON policy (policy_name);
CREATE INDEX IF NOT EXISTS ix_policy_region_code ON policy (region_code);
CREATE INDEX IF NOT EXISTS ix_policy_main_category ON policy (main_category);

CREATE TABLE IF NOT EXISTS users (
	user_id BIGSERIAL NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	nickname VARCHAR(100) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_nickname ON users (nickname);

CREATE TABLE IF NOT EXISTS chat_session (
	chat_session_id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	title VARCHAR(255), 
	session_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL, 
	last_message_at TIMESTAMP WITHOUT TIME ZONE, 
	latest_request_id BIGINT, 
	slot_json JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (chat_session_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eligibility_request (
	request_id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	source_type VARCHAR(30) DEFAULT 'POLICY_DETAIL' NOT NULL, 
	source_ref_id VARCHAR(100), 
	idempotency_key VARCHAR(120), 
	raw_query TEXT, 
	parsed_query_json JSONB, 
	merged_condition_json JSONB, 
	profile_conflict_json JSONB, 
	result_json JSONB, 
	error_message TEXT, 
	request_status VARCHAR(50) DEFAULT 'READY' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (request_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_eligibility_request_policy_id ON eligibility_request (policy_id);
CREATE INDEX IF NOT EXISTS ix_eligibility_request_user_id ON eligibility_request (user_id);
CREATE INDEX IF NOT EXISTS ix_eligibility_request_idempotency_key ON eligibility_request (idempotency_key);

CREATE TABLE IF NOT EXISTS family_member (
	family_member_id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	relation VARCHAR(50) NOT NULL, 
	birth_year INTEGER, 
	life_stage VARCHAR(50), 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (family_member_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_family_member_user_id ON family_member (user_id);

CREATE TABLE IF NOT EXISTS policy_checklist_template (
	template_item_id SERIAL NOT NULL, 
	policy_id BIGINT NOT NULL, 
	item_code VARCHAR(100) NOT NULL, 
	item_label VARCHAR(255) NOT NULL, 
	item_description TEXT, 
	is_required BOOLEAN NOT NULL, 
	display_order INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (template_item_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_condition_profile (
	condition_profile_id BIGSERIAL NOT NULL, 
	policy_id BIGINT NOT NULL, 
	condition_json JSONB DEFAULT '{}' NOT NULL, 
	target_summary TEXT, 
	confidence NUMERIC(5, 4), 
	review_required BOOLEAN DEFAULT 'false' NOT NULL, 
	quality_flags JSONB DEFAULT '[]' NOT NULL, 
	source_text TEXT, 
	source_fields JSONB DEFAULT '[]' NOT NULL, 
	extracted_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (condition_profile_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_policy_condition_profile_policy_id ON policy_condition_profile (policy_id);

CREATE TABLE IF NOT EXISTS policy_detail (
	policy_id BIGINT NOT NULL, 
	easy_summary TEXT, 
	target_description TEXT, 
	benefit_description TEXT, 
	application_method TEXT, 
	application_period_text TEXT, 
	caution TEXT, 
	PRIMARY KEY (policy_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_rule (
	rule_id BIGSERIAL NOT NULL, 
	policy_id BIGINT NOT NULL, 
	rule_type VARCHAR(50) NOT NULL, 
	operator VARCHAR(30) NOT NULL, 
	field_name VARCHAR(100) NOT NULL, 
	value_json JSONB NOT NULL, 
	is_hard_filter BOOLEAN DEFAULT 'true' NOT NULL, 
	manual_check_required BOOLEAN DEFAULT 'false' NOT NULL, 
	manual_check_reason TEXT, 
	note TEXT, 
	rule_group VARCHAR(100) DEFAULT 'ALL' NOT NULL, 
	group_operator VARCHAR(10) DEFAULT 'AND' NOT NULL, 
	source_text TEXT, 
	confidence NUMERIC(5, 4), 
	review_required BOOLEAN DEFAULT 'false' NOT NULL, 
	is_exclusion BOOLEAN DEFAULT 'false' NOT NULL, 
	origin VARCHAR(30) DEFAULT 'openapi' NOT NULL, 
	PRIMARY KEY (rule_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_policy_rule_policy_id ON policy_rule (policy_id);

CREATE TABLE IF NOT EXISTS recommendation_request (
	request_id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	source_type VARCHAR(30) DEFAULT 'FORM' NOT NULL, 
	source_ref_id VARCHAR(100), 
	idempotency_key VARCHAR(120), 
	raw_query TEXT, 
	parsed_query_json JSONB, 
	merged_condition_json JSONB, 
	profile_conflict_json JSONB, 
	result_json JSONB, 
	error_message TEXT, 
	request_status VARCHAR(50) DEFAULT 'READY' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (request_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_recommendation_request_idempotency_key ON recommendation_request (idempotency_key);
CREATE INDEX IF NOT EXISTS ix_recommendation_request_user_id ON recommendation_request (user_id);

CREATE TABLE IF NOT EXISTS user_favorites (
	user_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	saved_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (user_id, policy_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_policy_progress (
	progress_id SERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	progress_status VARCHAR(50) NOT NULL, 
	progress_percent INTEGER NOT NULL, 
	memo TEXT, 
	started_at TIMESTAMP WITHOUT TIME ZONE, 
	applied_at TIMESTAMP WITHOUT TIME ZONE, 
	decided_at TIMESTAMP WITHOUT TIME ZONE, 
	received_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (progress_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_profile (
	profile_id BIGSERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	region_code VARCHAR(50), 
	household_type VARCHAR(50), 
	income_bracket VARCHAR(50), 
	employment_status VARCHAR(50), 
	pregnancy_status BOOLEAN DEFAULT 'false' NOT NULL, 
	profile_json JSONB, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (profile_id), 
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_profile_user_id ON user_profile (user_id);

CREATE TABLE IF NOT EXISTS chat_message (
	chat_message_id BIGSERIAL NOT NULL, 
	chat_session_id BIGINT NOT NULL, 
	parent_message_id BIGINT, 
	role VARCHAR(20) NOT NULL, 
	message_type VARCHAR(30) DEFAULT 'TEXT' NOT NULL, 
	content TEXT, 
	structured_json JSONB, 
	sequence_no INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (chat_message_id), 
	FOREIGN KEY(chat_session_id) REFERENCES chat_session (chat_session_id) ON DELETE CASCADE, 
	FOREIGN KEY(parent_message_id) REFERENCES chat_message (chat_message_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS policy_assessment (
	assessment_id BIGSERIAL NOT NULL, 
	request_id BIGINT, 
	recommendation_request_id BIGINT, 
	eligibility_request_id BIGINT, 
	policy_id BIGINT NOT NULL, 
	assessment_type VARCHAR(50) NOT NULL, 
	assessment_status VARCHAR(50) NOT NULL, 
	confidence_score NUMERIC(5, 2), 
	matched_conditions_json JSONB, 
	missing_conditions_json JSONB, 
	conflicting_conditions_json JSONB, 
	manual_check_points_json JSONB, 
	reason_summary TEXT, 
	selected_for_result BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (assessment_id), 
	FOREIGN KEY(request_id) REFERENCES recommendation_request (request_id) ON DELETE CASCADE, 
	FOREIGN KEY(recommendation_request_id) REFERENCES recommendation_request (request_id) ON DELETE CASCADE, 
	FOREIGN KEY(eligibility_request_id) REFERENCES eligibility_request (request_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_document (
	document_id BIGSERIAL NOT NULL, 
	policy_id BIGINT NOT NULL, 
	condition_profile_id BIGINT, 
	source_title VARCHAR(255), 
	source_url TEXT, 
	source_type VARCHAR(50), 
	raw_text TEXT, 
	collected_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (document_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE, 
	FOREIGN KEY(condition_profile_id) REFERENCES policy_condition_profile (condition_profile_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS policy_summary_cache (
	summary_id BIGSERIAL NOT NULL, 
	policy_id BIGINT NOT NULL, 
	condition_profile_id BIGINT, 
	condition_profile_updated_at TIMESTAMP WITHOUT TIME ZONE, 
	summary_source VARCHAR(50) DEFAULT 'policy_condition_profile' NOT NULL, 
	request_status VARCHAR(50) DEFAULT 'PROCESSING' NOT NULL, 
	summary TEXT, 
	evidence_json JSONB, 
	error_message TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (summary_id), 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE, 
	FOREIGN KEY(condition_profile_id) REFERENCES policy_condition_profile (condition_profile_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_policy_summary_cache_condition_profile_id ON policy_summary_cache (condition_profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_policy_summary_cache_policy_id ON policy_summary_cache (policy_id);

CREATE TABLE IF NOT EXISTS recommendation_candidate (
	candidate_id BIGSERIAL NOT NULL, 
	request_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	filter_match_json JSONB, 
	retrieval_score NUMERIC(10, 4), 
	rerank_score NUMERIC(10, 4), 
	candidate_status VARCHAR(50) DEFAULT 'CANDIDATE' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (candidate_id), 
	CONSTRAINT recommendation_candidate_request_policy_uidx UNIQUE (request_id, policy_id), 
	FOREIGN KEY(request_id) REFERENCES recommendation_request (request_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_recommendation_candidate_request_id ON recommendation_candidate (request_id);
CREATE INDEX IF NOT EXISTS ix_recommendation_candidate_policy_id ON recommendation_candidate (policy_id);

CREATE TABLE IF NOT EXISTS user_policy_checklist_item (
	user_checklist_item_id SERIAL NOT NULL, 
	progress_id BIGINT NOT NULL, 
	template_item_id BIGINT NOT NULL, 
	item_status VARCHAR(30) NOT NULL, 
	checked_at TIMESTAMP WITHOUT TIME ZONE, 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (user_checklist_item_id), 
	FOREIGN KEY(progress_id) REFERENCES user_policy_progress (progress_id) ON DELETE CASCADE, 
	FOREIGN KEY(template_item_id) REFERENCES policy_checklist_template (template_item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_message_policy (
	chat_message_policy_id BIGSERIAL NOT NULL, 
	chat_message_id BIGINT NOT NULL, 
	policy_id BIGINT NOT NULL, 
	action_type VARCHAR(30) NOT NULL, 
	PRIMARY KEY (chat_message_policy_id), 
	FOREIGN KEY(chat_message_id) REFERENCES chat_message (chat_message_id) ON DELETE CASCADE, 
	FOREIGN KEY(policy_id) REFERENCES policy (policy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_request (
	request_id BIGSERIAL NOT NULL, 
	chat_session_id BIGINT NOT NULL, 
	user_message_id BIGINT NOT NULL, 
	idempotency_key VARCHAR(120), 
	status VARCHAR(30) DEFAULT 'processing' NOT NULL, 
	intent VARCHAR(50), 
	error_code VARCHAR(80), 
	error_message TEXT, 
	assistant_message_id BIGINT, 
	response_payload_json JSONB, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (request_id), 
	CONSTRAINT chat_request_session_idempotency_key_uk UNIQUE (chat_session_id, idempotency_key), 
	FOREIGN KEY(chat_session_id) REFERENCES chat_session (chat_session_id) ON DELETE CASCADE, 
	FOREIGN KEY(user_message_id) REFERENCES chat_message (chat_message_id) ON DELETE CASCADE, 
	FOREIGN KEY(assistant_message_id) REFERENCES chat_message (chat_message_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_request_chat_session_id ON chat_request (chat_session_id);

CREATE TABLE IF NOT EXISTS policy_document_chunk (
	chunk_id BIGSERIAL NOT NULL, 
	document_id BIGINT NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	chunk_text TEXT NOT NULL, 
	metadata_json JSONB, 
	PRIMARY KEY (chunk_id), 
	FOREIGN KEY(document_id) REFERENCES policy_document (document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assessment_evidence (
	evidence_id BIGSERIAL NOT NULL, 
	assessment_id BIGINT NOT NULL, 
	chunk_id BIGINT NOT NULL, 
	snippet TEXT, 
	similarity_score NUMERIC(10, 6), 
	evidence_role VARCHAR(50), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (evidence_id), 
	FOREIGN KEY(assessment_id) REFERENCES policy_assessment (assessment_id) ON DELETE CASCADE, 
	FOREIGN KEY(chunk_id) REFERENCES policy_document_chunk (chunk_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_message_evidence (
	chat_message_evidence_id BIGSERIAL NOT NULL, 
	chat_message_id BIGINT NOT NULL, 
	chunk_id BIGINT NOT NULL, 
	snippet TEXT, 
	evidence_role VARCHAR(30), 
	PRIMARY KEY (chat_message_evidence_id), 
	FOREIGN KEY(chat_message_id) REFERENCES chat_message (chat_message_id) ON DELETE CASCADE, 
	FOREIGN KEY(chunk_id) REFERENCES policy_document_chunk (chunk_id) ON DELETE CASCADE
);
COMMIT;
