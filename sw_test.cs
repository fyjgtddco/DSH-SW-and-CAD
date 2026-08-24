
using System;
using System.Runtime.InteropServices;
class SWTest {
    static void Main() {
        try {
            var sw = Marshal.GetActiveObject("SldWorks.Application");
            Console.WriteLine("SW版本: " + sw.RevisionNumber);
            var model = sw.ActiveDoc;
            if (model != null) {
                Console.WriteLine("文档: " + model.GetTitle);
                var fm = model.FeatureManager;
                // 测试 InsertRefPlane
                for (int n = 3; n <= 8; n++) {
                    double[] args = new double[n];
                    args[0] = 0; // Type
                    for (int i = 1; i < n; i++) args[i] = 0.015;
                    try {
                        var r = fm.InsertRefPlane(args[0], args.Length > 1 ? args[1] : 0,
                            args.Length > 2 ? args[2] : 0, args.Length > 3 ? args[3] : 0,
                            args.Length > 4 ? args[4] : 0, args.Length > 5 ? args[5] : 0,
                            args.Length > 6 ? args[6] : 0, args.Length > 7 ? args[7] : 0);
                        Console.WriteLine("InsertRefPlane(" + n + "): " + r);
                        if (r != null) {
                            Console.WriteLine("  ✅ name=" + r.GetTitle());
                            break;
                        }
                    } catch (Exception ex) {
                        Console.WriteLine("InsertRefPlane(" + n + "): " + ex.Message.Substring(0, Math.Min(60, ex.Message.Length)));
                    }
                }
            } else {
                Console.WriteLine("无活动文档");
            }
            Marshal.ReleaseComObject(sw);
        } catch (Exception e) {
            Console.WriteLine("ERROR: " + e.Message);
        }
    }
}
