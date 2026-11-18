import type { BuildSizeSubmissionManifestV1 } from '../src/contracts/submission.js';
import type { BuildSizeBuildMetaV1 } from '../src/contracts/build-meta.js';
import type { BuildSizeUnityReportV1 } from '../src/contracts/unity-report.js';

// 用编译检查保障 nullable 与必填字段语义，不把缺失大小变成 0。
declare const metadata: BuildSizeBuildMetaV1;
declare const report: BuildSizeUnityReportV1;
declare const manifest: BuildSizeSubmissionManifestV1;
const unityTotal: number | null = report.unity_reported_total_bytes;
const development: boolean | null = metadata.settings.development_build;
const bytes: number = manifest.artifacts[0].bytes;
void [unityTotal, development, bytes];

// @ts-expect-error 缺少必填的 schema_version/run_id 等字段。
const invalidManifest: BuildSizeSubmissionManifestV1 = {};
// @ts-expect-error v1 schema_version 只能是数值 1。
const invalidVersion: BuildSizeSubmissionManifestV1['schema_version'] = 2;
// @ts-expect-error 可空 total 不能作为已知 number 使用。
const invalidTotal: number = report.unity_reported_total_bytes;
void [invalidManifest, invalidVersion, invalidTotal];
