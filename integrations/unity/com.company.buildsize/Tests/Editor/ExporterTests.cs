using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using BuildSize.Editor;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEditor;

namespace BuildSize.Tests
{
    public class ExporterTests
    {
        string directory;
        [SetUp] public void Setup() { directory = Path.Combine(Directory.GetParent(Application.dataPath).FullName, ".buildsize-tests", Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory); }
        [TearDown] public void Cleanup() { if (Directory.Exists(directory)) Directory.Delete(directory, true); }

        [Test] public void JsonPreservesNullUnicodeAndIntegerBytes()
        {
            string temp = BoundedJson.WriteTemporary(directory, new Dictionary<string, object> {
                {"nullable", null}, {"text", "中文 \" \\ \n"}, {"big", BoundedJson.SafeInteger}, {"enabled", false}
            });
            string json = File.ReadAllText(temp);
            Assert.That(json, Does.Contain("\"nullable\":null"));
            Assert.That(json, Does.Contain("9007199254740991"));
            Assert.That(json, Does.Contain("中文 \\\" \\\\ \\n"));
            Assert.That(json, Does.Contain("\"enabled\":false"));
        }
        [Test] public void AtomicReplacementPreservesDestinationOnSerializationFailure()
        {
            string final = Path.Combine(directory, "report.json"); File.WriteAllText(final, "previous");
            Assert.Throws<InvalidDataException>(() => BoundedJson.WriteTemporary(directory, double.NaN));
            Assert.That(File.ReadAllText(final), Is.EqualTo("previous"));
            Assert.That(Directory.GetFiles(directory, "*.part"), Is.Empty);
            string temp = BoundedJson.WriteTemporary(directory, new Dictionary<string, object> { {"new", true} });
            BoundedJson.Commit(temp, final);
            Assert.That(File.ReadAllText(final), Does.Contain("\"new\":true"));
        }
        [Test] public void IntegerOverflowIsRejected()
        {
            Assert.Throws<InvalidDataException>(() => BoundedJson.Number(ulong.MaxValue));
        }
        [Test] public void BuildOptionNamesDoNotContainObsoleteZeroAliases()
        {
            var names = BuildSizeExporter.OptionNames(BuildOptions.DetailedBuildReport);
            Assert.That(names, Does.Contain("DetailedBuildReport"));
            Assert.That(names, Does.Not.Contain("Il2CPP"));
            Assert.That(names, Does.Not.Contain("CompressTextures"));
            Assert.That(names.All(name => !name.Contains(",")), Is.True);
        }
        [Test] public void DestinationMustBeOutsideAssetsWithValidRunId()
        {
            Assert.That(BuildSizeExporter.ValidateDestination(Application.dataPath, Guid.NewGuid().ToString()), Is.False);
            Assert.That(BuildSizeExporter.ValidateDestination(Path.Combine(Application.dataPath, "Reports"), Guid.NewGuid().ToString()), Is.False);
            Assert.That(BuildSizeExporter.ValidateDestination(directory, "invalid"), Is.False);
            Assert.That(BuildSizeExporter.ValidateDestination(directory, Guid.NewGuid().ToString()), Is.True);
        }
        [Test] public void DisabledCallbackHasNoFilesystemSideEffects()
        {
            string mode = Environment.GetEnvironmentVariable("BUILDSIZE_EXPORT_MODE");
            try
            {
                Environment.SetEnvironmentVariable("BUILDSIZE_EXPORT_MODE", null);
                Assert.That(BuildSizeExporter.ExportFromEnvironment(null), Is.False);
                Assert.That(Directory.GetFileSystemEntries(directory), Is.Empty);
            }
            finally { Environment.SetEnvironmentVariable("BUILDSIZE_EXPORT_MODE", mode); }
        }
        [Test] public void MissingCallbackContextDoesNotScanOrWrite()
        {
            string mode = Environment.GetEnvironmentVariable("BUILDSIZE_EXPORT_MODE");
            string runId = Environment.GetEnvironmentVariable("BUILDSIZE_RUN_ID");
            try
            {
                Environment.SetEnvironmentVariable("BUILDSIZE_EXPORT_MODE", "callback");
                Environment.SetEnvironmentVariable("BUILDSIZE_RUN_ID", null);
                Assert.That(BuildSizeExporter.ExportFromEnvironment(null), Is.False);
                Assert.That(Directory.GetFileSystemEntries(directory), Is.Empty);
            }
            finally
            {
                Environment.SetEnvironmentVariable("BUILDSIZE_EXPORT_MODE", mode);
                Environment.SetEnvironmentVariable("BUILDSIZE_RUN_ID", runId);
            }
        }
        [Test] public void ExportFailureDoesNotThrowOrChangeBuildResult()
        {
            LogAssert.Expect(LogType.Warning, new Regex("\\[BuildSize\\] 报告导出失败"));
            Assert.That(BuildSizeExporter.Export(null, directory, Guid.NewGuid().ToString()), Is.False);
            Assert.That(Directory.GetFileSystemEntries(directory), Is.Empty);
        }
    }
}
