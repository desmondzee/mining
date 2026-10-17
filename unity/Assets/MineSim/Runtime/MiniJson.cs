// Minimal JSON parser (no dependencies). Produces:
//   object -> Dictionary<string, object>, array -> List<object>, number -> double,
//   string -> string, true/false -> bool, null -> null.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace MineSim
{
    public static class MiniJson
    {
        public static object Parse(string json)
        {
            if (json == null) return null;
            var p = new Parser(json);
            p.SkipWs();
            object v = p.ParseValue();
            return v;
        }

        // ---------- typed helpers ----------
        public static Dictionary<string, object> Obj(object o, string key)
        {
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(key, out var v)) return null;
            return v as Dictionary<string, object>;
        }

        public static List<object> Arr(object o, string key)
        {
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(key, out var v)) return null;
            return v as List<object>;
        }

        public static string Str(object o, string key, string def = null)
        {
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(key, out var v) || v == null) return def;
            if (v is string s) return s;
            if (v is double dd) return dd.ToString(CultureInfo.InvariantCulture);
            if (v is bool b) return b ? "true" : "false";
            return def;
        }

        public static double Num(object o, string key, double def = 0)
        {
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(key, out var v) || v == null) return def;
            if (v is double dd) return dd;
            if (v is bool b) return b ? 1 : 0;
            if (v is string s && double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var r)) return r;
            return def;
        }

        public static bool Has(object o, string key)
        {
            var d = o as Dictionary<string, object>;
            return d != null && d.TryGetValue(key, out var v) && v != null;
        }

        public static bool Bool(object o, string key, bool def = false)
        {
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(key, out var v) || v == null) return def;
            if (v is bool b) return b;
            if (v is double dd) return dd != 0;
            return def;
        }

        /// <summary>Array of numbers -> double[] (non-numbers become 0). Returns null if not an array.</summary>
        public static double[] Nums(object arr)
        {
            var l = arr as List<object>;
            if (l == null) return null;
            var r = new double[l.Count];
            for (int i = 0; i < l.Count; i++) r[i] = l[i] is double d ? d : 0.0;
            return r;
        }

        private sealed class Parser
        {
            private readonly string s;
            private int i;

            public Parser(string json) { s = json; i = 0; }

            public void SkipWs()
            {
                while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            }

            public object ParseValue()
            {
                SkipWs();
                if (i >= s.Length) throw new FormatException("Unexpected end of JSON");
                char c = s[i];
                switch (c)
                {
                    case '{': return ParseObject();
                    case '[': return ParseArray();
                    case '"': return ParseString();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return ParseNumber();
                }
            }

            private void Expect(string word)
            {
                if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                    throw new FormatException("Invalid token at " + i);
                i += word.Length;
            }

            private Dictionary<string, object> ParseObject()
            {
                var d = new Dictionary<string, object>();
                i++; // {
                SkipWs();
                if (i < s.Length && s[i] == '}') { i++; return d; }
                while (true)
                {
                    SkipWs();
                    if (i >= s.Length || s[i] != '"') throw new FormatException("Expected key at " + i);
                    string key = ParseString();
                    SkipWs();
                    if (i >= s.Length || s[i] != ':') throw new FormatException("Expected ':' at " + i);
                    i++;
                    d[key] = ParseValue();
                    SkipWs();
                    if (i >= s.Length) throw new FormatException("Unterminated object");
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; return d; }
                    throw new FormatException("Expected ',' or '}' at " + i);
                }
            }

            private List<object> ParseArray()
            {
                var l = new List<object>();
                i++; // [
                SkipWs();
                if (i < s.Length && s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(ParseValue());
                    SkipWs();
                    if (i >= s.Length) throw new FormatException("Unterminated array");
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; return l; }
                    throw new FormatException("Expected ',' or ']' at " + i);
                }
            }

            private string ParseString()
            {
                i++; // opening quote
                var sb = new StringBuilder();
                while (i < s.Length)
                {
                    char c = s[i++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    if (i >= s.Length) break;
                    char e = s[i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (i + 4 > s.Length) throw new FormatException("Bad \\u escape");
                            sb.Append((char)int.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                            i += 4;
                            break;
                        default: sb.Append(e); break;
                    }
                }
                throw new FormatException("Unterminated string");
            }

            private object ParseNumber()
            {
                int start = i;
                while (i < s.Length)
                {
                    char c = s[i];
                    if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') i++;
                    else break;
                }
                if (start == i) throw new FormatException("Unexpected character '" + s[i] + "' at " + i);
                string tok = s.Substring(start, i - start);
                if (double.TryParse(tok, NumberStyles.Float, CultureInfo.InvariantCulture, out double v)) return v;
                // Python may emit NaN/Infinity (non-standard); not handled here -> 0
                return 0.0;
            }
        }
    }
}
