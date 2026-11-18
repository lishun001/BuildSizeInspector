/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeFindingV1 {
  schema_version: 1;
  rule_id: string;
  rule_version: string;
  confidence: 'observed' | 'correlated' | 'hypothesis';
  severity: 'info' | 'warning' | 'error';
  subject: {
    kind: 'apk_entry' | 'unity_asset' | 'config' | 'build' | 'container';
    key: string;
  };
  title: string;
  explanation: string;
  metric: 'zip_compressed_bytes' | 'unity_packed_bytes' | 'apk_overhead_bytes' | 'none';
  delta_bytes: number | null;
  additive: false;
  /**
   * @minItems 1
   */
  evidence: [
    {
      artifact_role:
        | 'apk'
        | 'build_meta'
        | 'unity_report'
        | 'asset_importers'
        | 'git_changes'
        | 'packages_lock'
        | 'derived_analysis';
      artifact_sha256: string | null;
      locator: string;
      summary: string;
    },
    ...{
      artifact_role:
        | 'apk'
        | 'build_meta'
        | 'unity_report'
        | 'asset_importers'
        | 'git_changes'
        | 'packages_lock'
        | 'derived_analysis';
      artifact_sha256: string | null;
      locator: string;
      summary: string;
    }[]
  ];
  suggested_checks: string[];
  review_state: 'unreviewed' | 'confirmed' | 'rejected';
}
