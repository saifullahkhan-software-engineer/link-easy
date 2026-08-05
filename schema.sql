--
-- PostgreSQL database dump
--



-- Dumped from database version 18.4
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: campaign_status; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.campaign_status AS ENUM (
    'DRAFT',
    'ACTIVE',
    'PAUSED',
    'COMPLETE',
    'FAILED'
);


ALTER TYPE public.campaign_status OWNER TO postgres;

--
-- Name: campaign_step_type; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.campaign_step_type AS ENUM (
    'VISIT_PROFILE',
    'LIKE_POST',
    'VISIT_AND_LIKE',
    'SEND_CONNECTION',
    'SEND_MESSAGE',
    'FOLLOW_UP_IF_PENDING',
    'THANKS_IF_ACCEPTED'
);


ALTER TYPE public.campaign_step_type OWNER TO postgres;

--
-- Name: job_status; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.job_status AS ENUM (
    'QUEUED',
    'RUNNING',
    'DONE',
    'FAILED',
    'SKIPPED'
);


ALTER TYPE public.job_status OWNER TO postgres;

--
-- Name: lead_status; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.lead_status AS ENUM (
    'PENDING',
    'VISITING',
    'REQUESTED',
    'ACCEPTED',
    'MESSAGED',
    'REPLIED',
    'SKIPPED',
    'FAILED'
);


ALTER TYPE public.lead_status OWNER TO postgres;

--
-- Name: linkedin_account_status; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.linkedin_account_status AS ENUM (
    'PENDING_VERIFICATION',
    'ACTIVE',
    'FAILED',
    'SUSPENDED'
);


ALTER TYPE public.linkedin_account_status OWNER TO postgres;

--
-- Name: feed_scroll_mode; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.feed_scroll_mode AS ENUM (
    'job_search',
    'post_search'
);


ALTER TYPE public.feed_scroll_mode OWNER TO postgres;

--
-- Name: feed_scroll_job_status; Type: TYPE; Schema: public; Owner: postgres
--

CREATE TYPE public.feed_scroll_job_status AS ENUM (
    'draft',
    'active',
    'paused'
);


ALTER TYPE public.feed_scroll_job_status OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: campaign_jobs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.campaign_jobs (
    id character varying NOT NULL,
    campaign_id character varying NOT NULL,
    lead_id character varying NOT NULL,
    step_type character varying NOT NULL,
    celery_task_id character varying,
    status public.job_status NOT NULL,
    action_message text,
    error_message text,
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.campaign_jobs OWNER TO postgres;

--
-- Name: campaign_steps; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.campaign_steps (
    id character varying NOT NULL,
    campaign_id character varying NOT NULL,
    step_order integer NOT NULL,
    step_type public.campaign_step_type NOT NULL,
    delay_hours integer NOT NULL,
    condition character varying
);


ALTER TABLE public.campaign_steps OWNER TO postgres;

--
-- Name: campaigns; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.campaigns (
    id character varying NOT NULL,
    account_email character varying NOT NULL,
    name character varying NOT NULL,
    description text,
    status public.campaign_status NOT NULL,
    search_filters json,
    daily_connection_limit integer,
    daily_message_limit integer,
    daily_visit_limit integer,
    connection_note_template text,
    message_templates json,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone
);


ALTER TABLE public.campaigns OWNER TO postgres;

--
-- Name: feed_scroll_jobs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.feed_scroll_jobs (
    id character varying NOT NULL,
    account_email character varying NOT NULL,
    owner_email character varying NOT NULL,
    name character varying NOT NULL,
    mode public.feed_scroll_mode DEFAULT 'job_search'::public.feed_scroll_mode NOT NULL,
    status public.feed_scroll_job_status DEFAULT 'draft'::public.feed_scroll_job_status NOT NULL,
    experience_min_years integer,
    experience_max_years integer,
    job_titles json,
    skill_set json,
    keywords json,
    feed_interval_hours integer DEFAULT 1 NOT NULL,
    posts_per_scan integer DEFAULT 10 NOT NULL,
    last_scanned_at timestamp with time zone,
    next_scan_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.feed_scroll_jobs OWNER TO postgres;

--
-- Name: feed_scroll_results; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.feed_scroll_results (
    id character varying NOT NULL,
    feed_scroll_job_id character varying NOT NULL,
    post_urn character varying,
    post_url character varying,
    author_name character varying,
    author_first_name character varying,
    author_last_name character varying,
    author_profile_url character varying,
    connection_degree character varying,
    post_time character varying,
    post_text text,
    score double precision DEFAULT 0 NOT NULL,
    matched_terms json,
    scan_batch_id character varying NOT NULL,
    scanned_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.feed_scroll_results OWNER TO postgres;

--
-- Name: leads; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.leads (
    id character varying NOT NULL,
    campaign_id character varying NOT NULL,
    linkedin_url character varying NOT NULL,
    first_name character varying,
    last_name character varying,
    headline character varying,
    status public.lead_status NOT NULL,
    current_step integer,
    connection_sent_at timestamp with time zone,
    accepted_at timestamp with time zone,
    last_action_at timestamp with time zone,
    next_action_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.leads OWNER TO postgres;

--
-- Name: linkedin_accounts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.linkedin_accounts (
    id character varying DEFAULT gen_random_uuid()::text NOT NULL,
    owner_email character varying,
    linkedin_email character varying NOT NULL,
    encrypted_password character varying NOT NULL,
    label character varying,
    status public.linkedin_account_status NOT NULL,
    profile_dir character varying NOT NULL,
    user_agent character varying,
    viewport_width integer,
    viewport_height integer,
    timezone_id character varying,
    locale character varying,
    hardware_concurrency integer,
    device_memory integer,
    warmup_stage character varying,
    proxy_host character varying,
    proxy_port character varying,
    proxy_username character varying,
    proxy_password_enc character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_linkedin_accounts_warmup_stage CHECK (((warmup_stage IS NULL) OR ((warmup_stage)::text = ANY ((ARRAY['new'::character varying, 'ramping'::character varying, 'established'::character varying])::text[]))))
);


ALTER TABLE public.linkedin_accounts OWNER TO postgres;

--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.password_reset_tokens (
    token_id character varying NOT NULL,
    email character varying NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


ALTER TABLE public.password_reset_tokens OWNER TO postgres;

--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    first_name character varying NOT NULL,
    last_name character varying NOT NULL,
    email character varying NOT NULL,
    hashed_password character varying NOT NULL,
    is_verified boolean NOT NULL,
    verification_code character varying,
    verification_code_expires_at timestamp with time zone,
    verification_attempt_count integer NOT NULL,
    verification_attempt_window_start timestamp with time zone,
    role character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: campaign_jobs campaign_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaign_jobs
    ADD CONSTRAINT campaign_jobs_pkey PRIMARY KEY (id);


--
-- Name: campaign_steps campaign_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaign_steps
    ADD CONSTRAINT campaign_steps_pkey PRIMARY KEY (id);


--
-- Name: campaigns campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_pkey PRIMARY KEY (id);


--
-- Name: feed_scroll_jobs feed_scroll_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_scroll_jobs
    ADD CONSTRAINT feed_scroll_jobs_pkey PRIMARY KEY (id);


--
-- Name: feed_scroll_results feed_scroll_results_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_scroll_results
    ADD CONSTRAINT feed_scroll_results_pkey PRIMARY KEY (id);


--
-- Name: leads leads_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_pkey PRIMARY KEY (id);


--
-- Name: linkedin_accounts linkedin_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.linkedin_accounts
    ADD CONSTRAINT linkedin_accounts_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (token_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (email);


--
-- Name: ix_campaign_jobs_campaign_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_campaign_jobs_campaign_id ON public.campaign_jobs USING btree (campaign_id);


--
-- Name: ix_campaign_jobs_lead_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_campaign_jobs_lead_id ON public.campaign_jobs USING btree (lead_id);


--
-- Name: ix_campaign_steps_campaign_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_campaign_steps_campaign_id ON public.campaign_steps USING btree (campaign_id);


--
-- Name: ix_feed_scroll_jobs_account_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_feed_scroll_jobs_account_email ON public.feed_scroll_jobs USING btree (account_email);


--
-- Name: ix_feed_scroll_jobs_owner_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_feed_scroll_jobs_owner_email ON public.feed_scroll_jobs USING btree (owner_email);


--
-- Name: ix_feed_scroll_results_feed_scroll_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_feed_scroll_results_feed_scroll_job_id ON public.feed_scroll_results USING btree (feed_scroll_job_id);


--
-- Name: ix_feed_scroll_results_post_urn; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_feed_scroll_results_post_urn ON public.feed_scroll_results USING btree (post_urn);


--
-- Name: ix_feed_scroll_results_scan_batch_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_feed_scroll_results_scan_batch_id ON public.feed_scroll_results USING btree (scan_batch_id);


--
-- Name: ix_leads_campaign_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_leads_campaign_id ON public.leads USING btree (campaign_id);


--
-- Name: ix_linkedin_accounts_linkedin_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_linkedin_accounts_linkedin_email ON public.linkedin_accounts USING btree (linkedin_email);


--
-- Name: ix_password_reset_tokens_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_password_reset_tokens_email ON public.password_reset_tokens USING btree (email);


--
-- Name: ix_password_reset_tokens_token_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_password_reset_tokens_token_id ON public.password_reset_tokens USING btree (token_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: campaign_jobs campaign_jobs_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaign_jobs
    ADD CONSTRAINT campaign_jobs_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id);


--
-- Name: campaign_jobs campaign_jobs_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaign_jobs
    ADD CONSTRAINT campaign_jobs_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: campaign_steps campaign_steps_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaign_steps
    ADD CONSTRAINT campaign_steps_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE CASCADE;


--
-- Name: campaigns campaigns_account_email_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_account_email_fkey FOREIGN KEY (account_email) REFERENCES public.linkedin_accounts(linkedin_email);


--
-- Name: feed_scroll_jobs feed_scroll_jobs_account_email_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_scroll_jobs
    ADD CONSTRAINT feed_scroll_jobs_account_email_fkey FOREIGN KEY (account_email) REFERENCES public.linkedin_accounts(linkedin_email);


--
-- Name: feed_scroll_jobs feed_scroll_jobs_owner_email_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_scroll_jobs
    ADD CONSTRAINT feed_scroll_jobs_owner_email_fkey FOREIGN KEY (owner_email) REFERENCES public.users(email) ON DELETE CASCADE;


--
-- Name: feed_scroll_results feed_scroll_results_feed_scroll_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.feed_scroll_results
    ADD CONSTRAINT feed_scroll_results_feed_scroll_job_id_fkey FOREIGN KEY (feed_scroll_job_id) REFERENCES public.feed_scroll_jobs(id) ON DELETE CASCADE;


--
-- Name: leads leads_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE CASCADE;


--
-- Name: linkedin_accounts linkedin_accounts_owner_email_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.linkedin_accounts
    ADD CONSTRAINT linkedin_accounts_owner_email_fkey FOREIGN KEY (owner_email) REFERENCES public.users(email) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

