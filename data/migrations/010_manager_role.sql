-- Migration 010: Manager role support
-- Adds manager_id to employees table and role column to tenant_api_keys

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS manager_id UUID REFERENCES employees(id);

CREATE INDEX IF NOT EXISTS idx_employees_manager_id ON employees (manager_id);

-- Add role column to public.tenant_api_keys (public schema — admin context)
-- Run this against the public schema, not a tenant schema.
ALTER TABLE public.tenant_api_keys
  ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'hr_admin'
    CHECK (role IN ('hr_admin', 'executive', 'analyst', 'manager'));
