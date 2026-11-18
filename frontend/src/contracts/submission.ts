/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeSubmissionManifestV1 {
  schema_version: 1;
  run_id: string;
  project_key: string;
  variant: string;
  ci: {
    instance_id: string;
    job_full_name: string;
    build_number: string;
    build_url: string;
  };
  /**
   * @minItems 2
   * @maxItems 6
   */
  artifacts:
    | [
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        }
      ]
    | [
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        }
      ]
    | [
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        }
      ]
    | [
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        }
      ]
    | [
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        },
        {
          role: 'apk' | 'build_meta' | 'unity_report' | 'asset_importers' | 'git_changes' | 'packages_lock';
          filename: string;
          bytes: number;
          sha256: string;
        }
      ];
}
