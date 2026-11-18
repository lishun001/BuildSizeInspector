using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace BuildSize.Editor
{
    // Editor-only JSON 写入器，明确输出 null，不经 JsonUtility 丢失可空字段。
    public static class BoundedJson
    {
        public const long SafeInteger = 9007199254740991L;
        public const long MaxBytes = 64L * 1024 * 1024;

        public static string WriteTemporary(string directory, object value)
        {
            string path = Path.Combine(directory, ".buildsize-" + Guid.NewGuid().ToString("N") + ".part");
            try
            {
                using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var limited = new LimitedStream(file, MaxBytes))
                using (var writer = new StreamWriter(limited, new UTF8Encoding(false, true)))
                {
                    Write(writer, value, 0);
                    writer.Write('\n');
                    writer.Flush();
                    file.Flush(true);
                }
                return path;
            }
            catch
            {
                if (File.Exists(path)) File.Delete(path);
                throw;
            }
        }

        public static void Commit(string temporary, string destination)
        {
            if (File.Exists(destination)) File.Replace(temporary, destination, null);
            else File.Move(temporary, destination);
        }

        public static string Sha256(string path)
        {
            using (var input = File.OpenRead(path))
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
        }

        public static long Number(ulong value)
        {
            if (value > (ulong)SafeInteger) throw new InvalidDataException("计数超过 JSON 安全整数范围");
            return (long)value;
        }

        static void Write(TextWriter writer, object value, int depth)
        {
            if (depth > 16) throw new InvalidDataException("JSON 嵌套过深");
            if (value == null) { writer.Write("null"); return; }
            if (value is string text) { String(writer, text); return; }
            if (value is bool boolean) { writer.Write(boolean ? "true" : "false"); return; }
            if (value is IDictionary<string, object> map)
            {
                writer.Write('{');
                bool first = true;
                foreach (string key in SortedKeys(map))
                {
                    if (!first) writer.Write(',');
                    first = false;
                    String(writer, key); writer.Write(':'); Write(writer, map[key], depth + 1);
                }
                writer.Write('}'); return;
            }
            if (value is IEnumerable list)
            {
                writer.Write('['); bool first = true;
                foreach (object item in list)
                {
                    if (!first) writer.Write(','); first = false;
                    Write(writer, item, depth + 1);
                }
                writer.Write(']'); return;
            }
            if (value is float || value is double)
            {
                double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                if (double.IsNaN(number) || double.IsInfinity(number)) throw new InvalidDataException("JSON 数值非有限");
                writer.Write(number.ToString("R", CultureInfo.InvariantCulture)); return;
            }
            if (value is int || value is long || value is uint || value is ulong)
            {
                decimal number = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                if (number < -SafeInteger || number > SafeInteger) throw new InvalidDataException("JSON 整数越界");
                writer.Write(number.ToString(CultureInfo.InvariantCulture)); return;
            }
            throw new InvalidDataException("不支持的 JSON 值类型");
        }

        static IEnumerable<string> SortedKeys(IDictionary<string, object> map)
        {
            var keys = new List<string>(map.Keys); keys.Sort(StringComparer.Ordinal); return keys;
        }

        static void String(TextWriter writer, string value)
        {
            writer.Write('"');
            foreach (char character in value)
            {
                switch (character)
                {
                    case '"': writer.Write("\\\""); break;
                    case '\\': writer.Write("\\\\"); break;
                    case '\n': writer.Write("\\n"); break;
                    case '\r': writer.Write("\\r"); break;
                    case '\t': writer.Write("\\t"); break;
                    default:
                        if (character < 32) writer.Write("\\u" + ((int)character).ToString("x4"));
                        else writer.Write(character);
                        break;
                }
            }
            writer.Write('"');
        }

        sealed class LimitedStream : Stream
        {
            readonly Stream target; readonly long maximum; long written;
            public LimitedStream(Stream target, long maximum) { this.target = target; this.maximum = maximum; }
            public override void Write(byte[] buffer, int offset, int count)
            {
                if (written + count > maximum) throw new InvalidDataException("JSON 输出超过 64 MiB，未发布报告");
                target.Write(buffer, offset, count); written += count;
            }
            public override void Flush() { target.Flush(); }
            public override bool CanRead => false;
            public override bool CanSeek => false;
            public override bool CanWrite => true;
            public override long Length => written;
            public override long Position { get => written; set => throw new NotSupportedException(); }
            public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
            public override void SetLength(long value) => throw new NotSupportedException();
        }
    }
}
