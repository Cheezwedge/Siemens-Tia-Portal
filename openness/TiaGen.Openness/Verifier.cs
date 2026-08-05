using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Siemens.Engineering;
using Siemens.Engineering.Compiler;

namespace TiaGen.Openness
{
    /// <summary>
    /// Compiles the software and turns the compiler result tree into a flat, readable
    /// report. A green compile is the only automatic evidence that generated code is
    /// actually valid, so this is the gate the whole pipeline aims at.
    /// </summary>
    internal static class Verifier
    {
        public class Result
        {
            public int Errors, Warnings;
            public string State = "unknown";
            public List<string> Lines = new List<string>();
            public bool Ok => Errors == 0;
        }

        public static Result Compile(IEngineeringServiceProvider software, string what)
        {
            var outcome = new Result();
            var compiler = software.GetService<ICompilable>();
            if (compiler == null)
            {
                Log.Error($"{what} does not offer ICompilable; nothing was compiled");
                outcome.Errors = 1;
                return outcome;
            }

            CompilerResult result = null;
            Log.Info($"compiling {what} (this takes a while) ...");
            Log.Try($"compiling {what}", () => result = compiler.Compile(), fatal: true);

            outcome.State = result.State.ToString();
            outcome.Errors = result.ErrorCount;
            outcome.Warnings = result.WarningCount;

            Flatten(result.Messages, 0, outcome.Lines);

            if (outcome.Errors == 0)
            {
                Log.Ok($"{what} compiled: state {outcome.State}, " +
                       $"{outcome.Warnings} warning(s), 0 errors");
            }
            else
            {
                Log.Error($"{what} compile FAILED: state {outcome.State}, " +
                          $"{outcome.Errors} error(s), {outcome.Warnings} warning(s)");
            }

            // Print the errors and warnings themselves; the informational tree is huge.
            foreach (var line in outcome.Lines.Where(IsInteresting).Take(60))
            {
                Log.Info(line);
            }
            var interesting = outcome.Lines.Count(IsInteresting);
            if (interesting > 60)
            {
                Log.Info($"... {interesting - 60} more compiler message(s); see the log file");
            }
            foreach (var line in outcome.Lines)
            {
                Log.Detail(line);
            }

            return outcome;
        }

        private static bool IsInteresting(string line)
        {
            return line.IndexOf("Error", StringComparison.OrdinalIgnoreCase) >= 0
                   || line.IndexOf("Warning", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static void Flatten(CompilerResultMessageComposition messages, int depth,
                                    List<string> into)
        {
            if (messages == null) return;
            foreach (CompilerResultMessage message in messages)
            {
                var indent = new string(' ', depth * 2);
                var counts = message.ErrorCount > 0 || message.WarningCount > 0
                    ? $" [{message.ErrorCount}E/{message.WarningCount}W]"
                    : string.Empty;
                into.Add($"{indent}{message.State}: {message.Description}{counts}");
                Flatten(message.Messages, depth + 1, into);
            }
        }

        public static string Summarise(IEnumerable<KeyValuePair<string, Result>> results)
        {
            var text = new StringBuilder();
            foreach (var entry in results)
            {
                text.AppendLine($"{entry.Key}: {entry.Value.State}, " +
                                $"{entry.Value.Errors} error(s), {entry.Value.Warnings} warning(s)");
            }
            return text.ToString();
        }
    }
}
