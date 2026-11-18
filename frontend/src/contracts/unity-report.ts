/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeUnityReportV1 {
  schema_version: 1;
  run_id: string;
  exporter_version: string;
  unity_version: string;
  captured_at: string;
  capture_mode: 'buildplayer_return' | 'postprocess_callback';
  build_result: 'Succeeded' | 'Failed' | 'Cancelled' | 'Unknown';
  detailed_report_requested: boolean | null;
  unity_reported_total_bytes: number | null;
  containers: {
    container_index: number;
    short_path: string;
    overhead_bytes: number;
    entries: {
      record_index: number;
      source_guid: string | null;
      source_path: string | null;
      asset_type: string;
      packed_bytes: number;
    }[];
  }[];
  output_files: {
    path: string;
    role: string;
    bytes: number;
  }[];
  warnings: string[];
}
