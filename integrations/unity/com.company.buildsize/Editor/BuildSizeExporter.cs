using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace BuildSize.Editor
{
    [InitializeOnLoad]
    public static class BuildSizeExporter
    {
        public const string Version = "0.1.0";
        const int MaxRecords = 100000;
        const int MaxImporters = 50000;
        static readonly int MainThread = Thread.CurrentThread.ManagedThreadId;
        internal static Dictionary<string, object> Map(params object[] items)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < items.Length; i += 2) result.Add((string)items[i], items[i + 1]);
            return result;
        }
        static object Nullable(string value) => string.IsNullOrEmpty(value) ? null : value;
        static string Utc() => DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);

        // 失败只告警，不改变游戏构建结果；调用方仍应按 BuildReport.summary.result 判定发包结果。
        public static bool Export(BuildReport report, string reportDirectory, string runId)
        {
            return Capture(report, reportDirectory, runId, "buildplayer_return");
        }

        // 构建入口知道本次 options.scenes 时可传入，避免猜测全局 EditorBuildSettings。
        public static bool Export(BuildReport report, string reportDirectory, string runId, string[] actualScenes)
        {
            return Capture(report, reportDirectory, runId, "buildplayer_return", actualScenes);
        }

        public static bool ExportFromEnvironment(BuildReport report)
        {
            if (Environment.GetEnvironmentVariable("BUILDSIZE_EXPORT_MODE") != "callback") return false;
            string directory = Environment.GetEnvironmentVariable("BUILDSIZE_REPORT_DIR");
            string runId = Environment.GetEnvironmentVariable("BUILDSIZE_RUN_ID");
            if (string.IsNullOrEmpty(directory) || string.IsNullOrEmpty(runId)) return false;
            return Capture(report, directory, runId, "postprocess_callback");
        }

        public static bool ValidateDestination(string directory, string runId)
        {
            if (!Guid.TryParseExact(runId, "D", out _)) return false;
            if (string.IsNullOrWhiteSpace(directory)) return false;
            string full = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar);
            string assets = Path.GetFullPath(Application.dataPath).TrimEnd(Path.DirectorySeparatorChar);
            if (full.Equals(assets, StringComparison.OrdinalIgnoreCase) || full.StartsWith(assets + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return false;
            for (var parent = new DirectoryInfo(full); parent != null; parent = parent.Parent)
                if (parent.Exists && (parent.Attributes & FileAttributes.ReparsePoint) != 0) return false;
            return true;
        }

        static bool Capture(BuildReport report, string reportDirectory, string runId, string mode, string[] actualScenes = null)
        {
            var watch = Stopwatch.StartNew();
            var temporaries = new List<string>();
            try
            {
                if (Thread.CurrentThread.ManagedThreadId != MainThread) throw new InvalidOperationException("必须从 Editor 主线程导出");
                if (report == null || !ValidateDestination(reportDirectory, runId)) throw new InvalidDataException("报告、run_id 或 Assets 外输出目录无效");
                string root = Path.GetFullPath(reportDirectory);
                Directory.CreateDirectory(root);
                using (var captureLock = new FileStream(Path.Combine(root, ".capture.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
                {
                    string marker = Path.Combine(root, ".buildsize-run-id");
                    if (File.Exists(marker) && File.ReadAllText(marker).Trim() != runId) throw new InvalidDataException("输出目录属于其他 run_id");
                    if (!File.Exists(marker))
                    {
                        if (new[] { "unity-report.json", "asset-importers.json", "unity-settings.json", "capture-complete.json" }.Any(name => File.Exists(Path.Combine(root, name))))
                            throw new InvalidDataException("输出目录已有无法确认身份的报告");
                        File.WriteAllText(marker, runId, new UTF8Encoding(false));
                    }
                    string receipt = Path.Combine(root, "capture-complete.json");
                    if (File.Exists(receipt)) File.Delete(receipt);
                    var warnings = new List<string>();
                    var sourcePaths = new SortedSet<string>(StringComparer.Ordinal);
                    var containers = new List<object>();
                    int records = 0;
                    bool truncated = false;
                    var packed = report.packedAssets;
                    for (int c = 0; c < packed.Length; c++)
                    {
                        if (c >= 10000) { truncated = true; break; }
                        var entries = new List<object>();
                        var contents = packed[c].contents;
                        for (int r = 0; r < contents.Length; r++)
                        {
                            if (records >= MaxRecords) { truncated = true; break; }
                            var info = contents[r];
                            string path = info.sourceAssetPath;
                            string guid = info.sourceAssetGUID.ToString().ToLowerInvariant();
                            string type = Convert.ToString((object)info.type, CultureInfo.InvariantCulture);
                            entries.Add(Map("record_index", r, "source_guid", guid, "source_path", Nullable(path),
                                            "asset_type", string.IsNullOrEmpty(type) ? "Unknown" : type,
                                            "packed_bytes", BoundedJson.Number(info.packedSize)));
                            if (!string.IsNullOrEmpty(path) && guid != new string('0', 32)) sourcePaths.Add(path);
                            records++;
                        }
                        containers.Add(Map("container_index", c, "short_path", packed[c].shortPath,
                                           "overhead_bytes", BoundedJson.Number(packed[c].overhead), "entries", entries));
                        if (truncated) break;
                    }
                    if (truncated) warnings.Add("BUILDSIZE_TRUNCATED: record limit reached");
                    var files = report.GetFiles().OrderBy(file => file.path, StringComparer.Ordinal).Select(file => (object)Map(
                        "path", file.path, "role", string.IsNullOrEmpty(file.role) ? "Unknown" : file.role,
                        "bytes", BoundedJson.Number(file.size))).ToList();
                    object reportedTotal = report.summary.result == BuildResult.Unknown && report.summary.totalSize == 0
                        ? null : (object)BoundedJson.Number(report.summary.totalSize);
                    var unity = Map("schema_version", 1, "run_id", runId, "exporter_version", Version,
                                    "unity_version", Application.unityVersion, "captured_at", Utc(), "capture_mode", mode,
                                    "build_result", report.summary.result.ToString(),
                                    "detailed_report_requested", (report.summary.options & BuildOptions.DetailedBuildReport) != 0,
                                    "unity_reported_total_bytes", reportedTotal, "containers", containers,
                                    "output_files", files, "warnings", warnings);
                    var payloads = new SortedDictionary<string, object>(StringComparer.Ordinal) {
                        { "unity-report.json", unity }, { "unity-settings.json", Settings(report, runId, actualScenes) }
                    };
                    if (!string.Equals(Environment.GetEnvironmentVariable("BUILDSIZE_IMPORTER_COLLECTION"), "false", StringComparison.OrdinalIgnoreCase))
                        payloads.Add("asset-importers.json", Importers(sourcePaths, runId));
                    warnings.Add("BUILDSIZE_CAPTURE_DURATION_MS:" + watch.ElapsedMilliseconds.ToString(CultureInfo.InvariantCulture));
                    var pending = new List<KeyValuePair<string, string>>();
                    foreach (var pair in payloads)
                    {
                        string temporary = BoundedJson.WriteTemporary(root, pair.Value);
                        temporaries.Add(temporary); pending.Add(new KeyValuePair<string, string>(temporary, Path.Combine(root, pair.Key)));
                    }
                    foreach (var pair in pending) BoundedJson.Commit(pair.Key, pair.Value);
                    var artifacts = pending.Select(pair => (object)Map("filename", Path.GetFileName(pair.Value),
                        "bytes", new FileInfo(pair.Value).Length, "sha256", BoundedJson.Sha256(pair.Value))).ToList();
                    string complete = BoundedJson.WriteTemporary(root, Map("schema_version", 1, "run_id", runId, "capture_mode", mode, "artifacts", artifacts, "duration_ms", watch.ElapsedMilliseconds));
                    temporaries.Add(complete); BoundedJson.Commit(complete, receipt);
                }
                return true;
            }
            catch (Exception error)
            {
                // 不输出任意异常消息或环境变量；第三方 Importer 可能把秘密写入异常文本。
                UnityEngine.Debug.LogWarning("[BuildSize] 报告导出失败：" + error.GetType().Name);
                return false;
            }
            finally
            {
                foreach (string path in temporaries)
                    try { if (File.Exists(path)) File.Delete(path); }
                    catch (Exception) { UnityEngine.Debug.LogWarning("[BuildSize] 临时文件清理失败"); }
            }
        }

        static Dictionary<string, object> Sample(AudioImporterSampleSettings value)
        {
            return Map("compression_format", value.compressionFormat.ToString(), "quality", value.quality,
                       "sample_rate_setting", value.sampleRateSetting.ToString(), "sample_rate_override", (long)value.sampleRateOverride,
                       "load_type", value.loadType.ToString());
        }

        static object Importers(SortedSet<string> paths, string runId)
        {
            var assets = new List<object>(); var warnings = new List<string>(); var guids = new HashSet<string>();
            int failed = 0, requested = 0;
            foreach (string path in paths)
            {
                string guid = AssetDatabase.AssetPathToGUID(path).ToLowerInvariant();
                if (string.IsNullOrEmpty(guid) || guid == new string('0', 32) || !guids.Add(guid)) continue;
                requested++;
                if (assets.Count >= MaxImporters) { failed++; continue; }
                try
                {
                    AssetImporter importer = AssetImporter.GetAtPath(path);
                    if (importer == null) { failed++; warnings.Add("BUILDSIZE_IMPORTER_FAILED:" + guid); continue; }
                    var settings = Map(); var unsupported = new List<string>();
                    if (importer is TextureImporter texture)
                    {
                        var android = texture.GetPlatformTextureSettings("Android");
                        settings = Map("texture_type", texture.textureType.ToString(), "mipmap_enabled", texture.mipmapEnabled,
                                       "is_readable", texture.isReadable, "srgb", texture.sRGBTexture,
                                       "android", Map("overridden", android.overridden, "max_texture_size", android.maxTextureSize,
                                                      "format", android.format.ToString(), "compression_quality", android.compressionQuality,
                                                      "crunched_compression", android.crunchedCompression));
                        unsupported.Add("effective_texture_format");
                    }
                    else if (importer is AudioImporter audio)
                    {
                        var android = Sample(audio.GetOverrideSampleSettings("Android"));
                        android.Add("overridden", audio.ContainsSampleSettingsOverride("Android"));
                        settings = Map("force_to_mono", audio.forceToMono, "default", Sample(audio.defaultSampleSettings), "android", android);
                    }
                    string source = Path.GetFullPath(Path.Combine(Directory.GetParent(Application.dataPath).FullName, path));
                    string meta = source + ".meta";
                    assets.Add(Map("source_guid", guid, "source_path", path, "importer_type", importer.GetType().Name,
                                   "dependency_hash", AssetDatabase.GetAssetDependencyHash(path).ToString(),
                                   "source_sha256", OptionalHash(source, "source_sha256", unsupported),
                                   "meta_sha256", OptionalHash(meta, "meta_sha256", unsupported),
                                   "settings", settings, "unsupported_fields", unsupported));
                }
                catch (Exception) { failed++; warnings.Add("BUILDSIZE_IMPORTER_FAILED:" + guid); }
            }
            if (requested > MaxImporters) warnings.Add("BUILDSIZE_TRUNCATED: importer limit reached");
            return Map("schema_version", 1, "run_id", runId, "unity_version", Application.unityVersion,
                       "captured_at", Utc(), "coverage", Map("requested_count", requested, "collected_count", assets.Count, "failed_count", failed),
                       "assets", assets, "warnings", warnings);
        }

        static object Settings(BuildReport report, string runId, string[] actualScenes)
        {
            var warnings = new List<string>();
            var group = BuildPipeline.GetBuildTargetGroup(report.summary.platform);
            var named = NamedBuildTarget.FromBuildTargetGroup(group);
            bool android = report.summary.platform == BuildTarget.Android;
            string backend = PlayerSettings.GetScriptingBackend(named).ToString();
            object architectures = null;
            if (android)
            {
                var abi = new List<string>(); int flags = (int)PlayerSettings.Android.targetArchitectures;
                foreach (AndroidArchitecture value in Enum.GetValues(typeof(AndroidArchitecture)))
                {
                    if ((flags & (int)value) == 0) continue;
                    switch (value.ToString())
                    {
                        case "ARMv7": abi.Add("armeabi-v7a"); break;
                        case "ARM64": abi.Add("arm64-v8a"); break;
                        case "X86": abi.Add("x86"); break;
                        case "X86_64": abi.Add("x86_64"); break;
                    }
                }
                architectures = abi.Count == 0 ? null : (object)abi;
            }
            var options = report.summary.options;
            string compression = (options & BuildOptions.CompressWithLz4HC) != 0 ? "LZ4HC"
                : (options & BuildOptions.CompressWithLz4) != 0 ? "LZ4" : null;
            if (compression == null) warnings.Add("BUILDSIZE_SETTING_UNAVAILABLE:player_compression");
            var sceneGuids = new SortedSet<string>(StringComparer.Ordinal);
            if (actualScenes == null) warnings.Add("BUILDSIZE_SETTING_UNAVAILABLE:scene_guids; 未猜测 EditorBuildSettings 场景");
            else foreach (string scene in actualScenes)
            {
                string guid = AssetDatabase.AssetPathToGUID(scene);
                if (string.IsNullOrEmpty(guid)) warnings.Add("BUILDSIZE_SETTING_UNAVAILABLE:scene_guid");
                else sceneGuids.Add(guid.ToLowerInvariant());
            }
            return Map("schema_version", 1, "run_id", runId, "exporter_version", Version, "captured_at", Utc(),
                       "platform", report.summary.platform.ToString(), "application_id", PlayerSettings.GetApplicationIdentifier(named),
                       "version_name", PlayerSettings.bundleVersion, "version_code", android ? (object)PlayerSettings.Android.bundleVersionCode : null,
                       "build_completed_at", report.summary.buildEndedAt.Year < 2000 ? null : report.summary.buildEndedAt.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture),
                       "settings", Map("unity_version", Application.unityVersion,
                           "scripting_backend", backend == "Mono2x" ? "Mono" : backend == "IL2CPP" ? "IL2CPP" : null,
                           "architectures", architectures, "development_build", (options & BuildOptions.Development) != 0,
                           "managed_stripping_level", PlayerSettings.GetManagedStrippingLevel(named).ToString(),
                           "strip_engine_code", PlayerSettings.stripEngineCode, "player_compression", compression,
                           "il2cpp_code_generation", backend == "IL2CPP" ? (object)PlayerSettings.GetIl2CppCodeGeneration(named).ToString() : null,
                           "min_sdk", android ? (object)(int)PlayerSettings.Android.minSdkVersion : null,
                           "target_sdk_requested", android ? ((int)PlayerSettings.Android.targetSdkVersion == 0 ? "Auto" : ((int)PlayerSettings.Android.targetSdkVersion).ToString(CultureInfo.InvariantCulture)) : null,
                           "build_options", OptionNames(options),
                           "export_mode", android ? (EditorUserBuildSettings.exportAsGoogleAndroidProject ? "export_gradle" : "direct_apk") : null),
                       "content_policy", Map("builtin_policy_hash", null, "scene_guids", sceneGuids.ToList()), "warnings", warnings);
        }

        public static List<string> OptionNames(BuildOptions options)
        {
            var names = new List<string>();
            foreach (string name in Enum.GetNames(typeof(BuildOptions)))
            {
                var value = (BuildOptions)Enum.Parse(typeof(BuildOptions), name);
                if (value != 0 && (options & value) == value && !Attribute.IsDefined(typeof(BuildOptions).GetField(name), typeof(ObsoleteAttribute)))
                    names.Add(name);
            }
            names.Sort(StringComparer.Ordinal);
            return names;
        }

        static string OptionalHash(string path, string field, List<string> unsupported)
        {
            if (!File.Exists(path) || new FileInfo(path).Length > 64L * 1024 * 1024)
            { unsupported.Add(field); return null; }
            return BoundedJson.Sha256(path);
        }
    }

    public sealed class BuildSizePostprocessor : IPostprocessBuildWithReport
    {
        public int callbackOrder => 10000;
        public void OnPostprocessBuild(BuildReport report) { BuildSizeExporter.ExportFromEnvironment(report); }
    }
}
