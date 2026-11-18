/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeGitChangesV1 {
  schema_version: 1;
  run_id: string;
  requested_base_build_id: string | null;
  repositories: {
    repo_key: string;
    base_revision: string | null;
    head_revision: string;
    range_status: 'complete' | 'no_baseline' | 'missing_base' | 'unrelated_history' | 'failed';
    is_ancestor: boolean | null;
    changes: {
      status: 'A' | 'M' | 'D' | 'R' | 'C' | 'T' | 'U' | 'X';
      old_path: string | null;
      new_path: string | null;
      old_blob: string | null;
      new_blob: string | null;
      source_guid: string | null;
    }[];
    commit_ids: string[];
  }[];
  warnings: string[];
}
