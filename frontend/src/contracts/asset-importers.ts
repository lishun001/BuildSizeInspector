/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeAssetImportersV1 {
  schema_version: 1;
  run_id: string;
  unity_version: string;
  captured_at: string;
  coverage: {
    requested_count: number;
    collected_count: number;
    failed_count: number;
  };
  assets: {
    source_guid: string;
    source_path: string;
    importer_type: string;
    dependency_hash: string | null;
    source_sha256: string | null;
    meta_sha256: string | null;
    settings: {
      [k: string]: unknown;
    };
    unsupported_fields: string[];
  }[];
  warnings: string[];
}
