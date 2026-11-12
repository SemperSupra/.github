"""No-login SDK interface qualification for the reviewed Kaggle 2.2.4 client."""
import inspect
import unittest

class SharedKaggleSDKTests(unittest.TestCase):
    def test_bound_sdk_api_is_importable(self):
        from kaggle import api
        for symbol in ("authenticate","get_config_value","quota_view",
                       "kernels_list_with_response","kernels_status",
                       "kernels_push","kernels_output","build_kaggle_client"):
            with self.subTest(symbol=symbol):
                self.assertTrue(callable(getattr(api,symbol,None)))

    def test_submit_signature(self):
        from kaggle import api
        fields=inspect.signature(api.kernels_push).parameters
        for name in ("folder","timeout","acc"):
            self.assertIn(name,fields)

    def test_exact_native_version_request_import(self):
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
        r=ApiGetKernelRequest()
        r.user_name="synthetic";r.kernel_slug="synthetic"
        self.assertEqual((r.user_name,r.kernel_slug),("synthetic","synthetic"))

if __name__=="__main__":
    unittest.main()
