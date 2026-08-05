using System;
using System.Collections.Generic;
using System.IO;

namespace TiaGen.Openness
{
    /// <summary>
    /// Console + file logging. Every Openness call this tool makes is logged with
    /// what was attempted, because the usual failure mode is "this attribute is
    /// named differently in your TIA version" and the log is what tells you which.
    /// </summary>
    internal static class Log
    {
        private static StreamWriter _file;
        private static readonly List<string> Problems = new List<string>();

        public static bool Verbose { get; set; }

        public static void OpenFile(string path)
        {
            if (string.IsNullOrEmpty(path)) return;
            var directory = Path.GetDirectoryName(Path.GetFullPath(path));
            if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            _file = new StreamWriter(path, false) { AutoFlush = true };
            _file.WriteLine("# TiaGen.Openness log " + DateTime.Now.ToString("s"));
        }

        public static void Close()
        {
            _file?.Dispose();
            _file = null;
        }

        public static IReadOnlyList<string> Collected => Problems;

        public static void Stage(string name)
        {
            Write(Console.Out, ConsoleColor.Cyan, "== " + name);
        }

        public static void Info(string message)
        {
            Write(Console.Out, null, "   " + message);
        }

        public static void Detail(string message)
        {
            if (Verbose) Write(Console.Out, ConsoleColor.DarkGray, "     " + message);
            else _file?.WriteLine("     " + message);
        }

        public static void Ok(string message)
        {
            Write(Console.Out, ConsoleColor.Green, "   +  " + message);
        }

        public static void Skip(string message)
        {
            Write(Console.Out, ConsoleColor.DarkGray, "   -  " + message);
        }

        public static void Warn(string message)
        {
            Problems.Add("WARNING " + message);
            Write(Console.Error, ConsoleColor.Yellow, "   !  " + message);
        }

        public static void Error(string message)
        {
            Problems.Add("ERROR   " + message);
            Write(Console.Error, ConsoleColor.Red, "   X  " + message);
        }

        private static void Write(TextWriter writer, ConsoleColor? colour, string text)
        {
            _file?.WriteLine(text);
            if (colour.HasValue && !Console.IsOutputRedirected)
            {
                var previous = Console.ForegroundColor;
                Console.ForegroundColor = colour.Value;
                writer.WriteLine(text);
                Console.ForegroundColor = previous;
            }
            else
            {
                writer.WriteLine(text);
            }
        }

        /// <summary>
        /// Runs an Openness call, turning an exception into a logged problem rather
        /// than aborting the whole run. Returns true when the call succeeded.
        /// </summary>
        public static bool Try(string what, Action action, bool fatal = false)
        {
            try
            {
                action();
                Detail("ok: " + what);
                return true;
            }
            catch (Exception ex)
            {
                var message = what + " failed: " + ex.GetType().Name + ": " + ex.Message;
                if (fatal) throw new OpennessStepException(message, ex);
                Warn(message);
                return false;
            }
        }
    }

    internal class OpennessStepException : Exception
    {
        public OpennessStepException(string message, Exception inner) : base(message, inner) { }
        public OpennessStepException(string message) : base(message) { }
    }
}
