// 仅复制到独立验证工程的 Assets/Editor，不安装到用户游戏构建入口。
using System;
using System.IO;
using System.Linq;
using BuildSize.Editor;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildSizeValidation
{
    const string TexturePath = "Assets/BuildSizeFixture/Resources/中文 TestTexture.png";
    const string ScenePath = "Assets/BuildSizeFixture/Scene.unity";
    public static void DumpFlags()
    {
        var values = new System.Collections.Generic.Dictionary<string, object>();
        foreach (string name in Enum.GetNames(typeof(BuildOptions))) values.Add(name, Convert.ToInt64(Enum.Parse(typeof(BuildOptions), name)));
        string root = Environment.GetEnvironmentVariable("BUILDSIZE_VALIDATION_OUTPUT");
        BoundedJson.Commit(BoundedJson.WriteTemporary(root, values), Path.Combine(root, "build-options.json"));
    }
    public static void Run()
    {
        string root = Environment.GetEnvironmentVariable("BUILDSIZE_VALIDATION_OUTPUT");
        if (string.IsNullOrEmpty(root)) throw new InvalidOperationException("缺少验证输出目录");
        Directory.CreateDirectory(root);
        EditorUserBuildSettings.buildAppBundle = false;
        EditorUserBuildSettings.exportAsGoogleAndroidProject = false;
        PlayerSettings.Android.useCustomKeystore = false;
        PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, "com.company.buildsize.validation");
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.Mono2x);
        PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARMv7;
        PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel23;
        PlayerSettings.Android.targetSdkVersion = AndroidSdkVersions.AndroidApiLevel35;
        PlayerSettings.bundleVersion = "0.1.0";
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        Directory.CreateDirectory("Assets/BuildSizeFixture/Resources");
        EditorSceneManager.SaveScene(scene, ScenePath);
        string guid = null;
        for (int iteration = 0; iteration < 2; iteration++)
        {
            int size = iteration == 0 ? 32 : 128;
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false);
            var pixels = new Color32[size * size]; var random = new System.Random(1234);
            for (int i = 0; i < pixels.Length; i++) pixels[i] = new Color32((byte)random.Next(256), (byte)random.Next(256), (byte)random.Next(256), 255);
            texture.SetPixels32(pixels); texture.Apply(); File.WriteAllBytes(TexturePath, texture.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(texture);
            AssetDatabase.ImportAsset(TexturePath, ImportAssetOptions.ForceSynchronousImport);
            var importer = (TextureImporter)AssetImporter.GetAtPath(TexturePath);
            importer.mipmapEnabled = false; importer.isReadable = false;
            var android = importer.GetPlatformTextureSettings("Android");
            android.overridden = true; android.format = TextureImporterFormat.RGBA32; android.maxTextureSize = size;
            android.crunchedCompression = false; android.compressionQuality = 50;
            importer.SetPlatformTextureSettings(android); importer.SaveAndReimport();
            string current = AssetDatabase.AssetPathToGUID(TexturePath);
            if (guid != null && current != guid) throw new InvalidOperationException("改资源时 GUID 被改变");
            guid = current;
            string directory = Path.Combine(root, iteration == 0 ? "a" : "b");
            Directory.CreateDirectory(directory);
            string runId = Guid.NewGuid().ToString("D");
            Environment.SetEnvironmentVariable("BUILDSIZE_RUN_ID", runId);
            Environment.SetEnvironmentVariable("BUILDSIZE_REPORT_DIR", directory);
            Environment.SetEnvironmentVariable("BUILDSIZE_EXPORT_MODE", iteration == 0 ? "explicit" : "callback");
            PlayerSettings.Android.bundleVersionCode = iteration + 1;
            var options = new BuildPlayerOptions { scenes = new[] { ScenePath }, target = BuildTarget.Android,
                locationPathName = Path.Combine(directory, "fixture.apk"), options = BuildOptions.None };
            options.options |= BuildOptions.DetailedBuildReport;
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded) throw new InvalidOperationException("实际 Android 构建失败");
            string before = EditorJsonUtility.ToJson(importer);
            if (iteration == 1)
            {
                if (!File.Exists(Path.Combine(directory, "capture-complete.json"))) throw new InvalidOperationException("回调没有导出");
                File.Copy(Path.Combine(directory, "unity-report.json"), Path.Combine(root, "callback-report.json"), true);
            }
            if (!BuildSizeExporter.Export(report, directory, runId, options.scenes)) throw new InvalidOperationException("显式导出失败");
            if (!File.ReadAllText(Path.Combine(directory, "unity-settings.json")).Contains(AssetDatabase.AssetPathToGUID(ScenePath)))
                throw new InvalidOperationException("实际场景 GUID 未导出");
            if (EditorJsonUtility.ToJson(importer) != before) throw new InvalidOperationException("导出器改变了 Importer");
            string hash = BoundedJson.Sha256(Path.Combine(directory, "unity-report.json"));
            if (BuildSizeExporter.Export(report, directory, Guid.NewGuid().ToString("D"))) throw new InvalidOperationException("跨 run_id 覆盖被错误允许");
            if (BoundedJson.Sha256(Path.Combine(directory, "unity-report.json")) != hash) throw new InvalidOperationException("跨 run_id 请求覆盖了旧报告");
            if (report.summary.result != BuildResult.Succeeded) throw new InvalidOperationException("采集故障改变了构建结果");
        }
        AssetDatabase.Refresh();
        UnityEngine.Debug.Log("BuildSize real Android validation completed; texture GUID=" + guid);
    }
}
