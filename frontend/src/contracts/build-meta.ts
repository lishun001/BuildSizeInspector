/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */

export interface BuildSizeBuildMetaV1 {
  schema_version: 1;
  run_id: string;
  project_key: string;
  variant: string;
  platform: 'Android';
  artifact_type: 'APK';
  application_id: string;
  version_name: string;
  version_code: number;
  channel: string;
  branch: string | null;
  build_completed_at: string;
  ci: {
    instance_id: string;
    job_full_name: string;
    build_number: string;
    build_url: string;
  };
  settings: {
    unity_version: string | null;
    scripting_backend: 'IL2CPP' | 'Mono' | null;
    architectures:
      ['arm64-v8a' | 'armeabi-v7a' | 'x86' | 'x86_64', ...('arm64-v8a' | 'armeabi-v7a' | 'x86' | 'x86_64')[]] | null;
    development_build: boolean | null;
    managed_stripping_level: string | null;
    strip_engine_code: boolean | null;
    player_compression: string | null;
    il2cpp_code_generation: string | null;
    min_sdk: number | null;
    target_sdk_requested: string | null;
    build_options: string[];
    export_mode: 'direct_apk' | 'export_gradle';
  };
  /**
   * @minItems 1
   */
  repositories: [
    {
      repo_key: string;
      revision: string;
      dirty: boolean;
      relative_path: string;
      kind: 'main' | 'submodule' | 'art' | 'config' | 'other';
    },
    ...{
      repo_key: string;
      revision: string;
      dirty: boolean;
      relative_path: string;
      kind: 'main' | 'submodule' | 'art' | 'config' | 'other';
    }[]
  ];
  toolchain: {
    java_version: string | null;
    gradle_version: string | null;
    agp_version: string | null;
    ndk_version: string | null;
    android_build_tools_version: string | null;
    exporter_version: string | null;
  };
  content_policy: {
    builtin_policy_hash: string | null;
    scene_guids: string[];
  };
  warnings: string[];
}
