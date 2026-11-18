import type { BuildSizeBuildMetaV1 as BuildMeta } from "./contracts/build-meta";
import type { BuildSizeFindingV1 as Finding } from "./contracts/finding";
export type Page<T> = {
  items: T[];
  next_offset: number | null;
  state?: string;
  truncated?: boolean;
  rules_version?: number;
};
export type Project = {
  key: string;
  name: string;
  role: string;
  environment: string;
  retention_days: { apk: number; reports: number; metrics: number };
  raw_protection: string;
};
export type History = {
  build_id: string;
  build_number: string;
  variant: string;
  upload_state: string;
  analysis_state: string;
  build_completed_at: string | null;
  apk_file_bytes: number | null;
  analysis_revision: string | null;
  compatibility_key: string | null;
  comparison_id: string | null;
  delta_apk_bytes: number | null;
  finding_count: number | null;
};
export type Build = {
  pinned: boolean;
  build_id: string;
  run_id: string;
  variant: string;
  upload_state: string;
  analysis_state: string;
  analysis_revision: string | null;
  metadata: BuildMeta | null;
  result: {
    apk: {
      apk_file_bytes: number;
      zip_payload_bytes: number;
      apk_overhead_bytes: number;
      categories: { category: string; compressed_bytes: number }[];
    };
    coverage: Record<string, string>;
    warnings: string[];
    unity: Record<string, unknown>;
    comparison_id: string;
  } | null;
  artifacts: {
    role: string;
    bytes: number;
    sha256: string;
    state: string;
    schema_state: string | null;
    data_error: string | null;
    expired_at: string | null;
  }[];
  jobs: { state: string; error_code: string | null }[];
  distribution: {
    pgyer?: { publish_state: string; install_page_url: string | null };
  };
};
export type Comparison = {
  comparison_id: string;
  status: string;
  comparable: boolean;
  base_build_id: string | null;
  target_build_id: string;
  selection_reason: string;
  error_code: string | null;
  result: {
    old_apk_bytes: number | null;
    new_apk_bytes: number;
    delta_apk_bytes: number | null;
    growth_percent: number | null;
    delta_overhead_bytes: number | null;
    categories: { category: string; delta_bytes: number }[];
    quality_gate: string;
    absolute_budget_gate: string;
    finding_count: number;
    config_changes: {
      field: string;
      old: unknown;
      new: unknown;
      old_missing: boolean;
      new_missing: boolean;
    }[];
    unity: {
      status: string;
      delta_packed_bytes: number | null;
      importer_comparison_status?: string;
    };
    git_evidence_status: string;
    warnings: string[];
  } | null;
};
export type FindingItem = {
  id: string;
  review_version: number;
  finding: Finding;
};
export type EvidenceDetail = FindingItem & {
  evidence: {
    artifact_role: string;
    artifact_sha256: string | null;
    locator: string;
    summary: string;
    sources: {
      build_id: string;
      revision_id: string;
      download_url?: string;
      value: unknown;
      state: string;
    }[];
  }[];
};
export type Rules = {
  version: number;
  settings: {
    absolute_budget_bytes: number | null;
    growth_bytes: number;
    growth_basis_points: number;
    fixed_baseline_id: string | null;
  };
};
export type Entry = {
  entry_index: number;
  normalized_path: string;
  compressed_bytes: number;
  uncompressed_bytes: number;
  content_sha256: string;
  category: string;
};
export type Change = {
  path: string;
  status: string;
  old_bytes: number | null;
  new_bytes: number | null;
  delta_bytes: number | null;
};
export type UnityAsset = {
  group_key: string;
  key?: string;
  source_guid: string | null;
  source_paths: string[];
  asset_type: string;
  packed_bytes: number;
};
export type UnityChange = {
  group_key: string;
  source_guid: string | null;
  asset_type: string | null;
  old_paths: string[];
  new_paths: string[];
  renamed: boolean | null;
  status: string;
  old_packed_bytes: number | null;
  new_packed_bytes: number | null;
  delta_packed_bytes: number | null;
  details?: unknown;
};
