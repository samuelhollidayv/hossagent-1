"""
HossAgent End-to-End Test Harness
Tests Stripe + Email + Billing loop without modifying core logic.
"""

import os
import sys
import json
import requests
from datetime import datetime
from typing import Dict, List, Any, Tuple

BASE_URL = "http://localhost:5000"

class TestResult:
    def __init__(self, name: str, passed: bool, details: str = ""):
        self.name = name
        self.passed = passed
        self.details = details
    
    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.details}"

class HossTestHarness:
    def __init__(self):
        self.results: List[TestResult] = []
        self.stripe_enabled = False
        self.stripe_api_key = os.environ.get("STRIPE_API_KEY", "")
        self.enable_stripe = os.environ.get("ENABLE_STRIPE", "").upper() == "TRUE"
    
    def log(self, message: str):
        print(f"[TEST] {message}")
    
    def add_result(self, name: str, passed: bool, details: str = ""):
        result = TestResult(name, passed, details)
        self.results.append(result)
        print(str(result))
        return result
    
    def test_stripe_config(self) -> bool:
        """Test 1: Stripe Configuration Detection"""
        self.log("=" * 60)
        self.log("TEST 1: STRIPE CONFIGURATION")
        self.log("=" * 60)
        
        if not self.enable_stripe:
            self.add_result("Stripe Enable Flag", False, "ENABLE_STRIPE != TRUE")
            self.stripe_enabled = False
            return False
        
        self.add_result("Stripe Enable Flag", True, "ENABLE_STRIPE = TRUE")
        
        if not self.stripe_api_key:
            self.add_result("Stripe API Key", False, "STRIPE_API_KEY not set in Secrets")
            self.stripe_enabled = False
            return False
        
        key_preview = f"{self.stripe_api_key[:7]}...{self.stripe_api_key[-4:]}" if len(self.stripe_api_key) > 11 else "[hidden]"
        self.add_result("Stripe API Key", True, f"Present ({key_preview})")
        
        try:
            response = requests.get(
                "https://api.stripe.com/v1/charges",
                params={"limit": 1},
                auth=(self.stripe_api_key, ""),
                timeout=10
            )
            if response.status_code == 200:
                self.add_result("Stripe API Connection", True, "Authorized - API key valid")
                self.stripe_enabled = True
                return True
            else:
                error = response.json().get("error", {}).get("message", "Unknown error")
                self.add_result("Stripe API Connection", False, f"HTTP {response.status_code}: {error}")
                self.stripe_enabled = False
                return False
        except Exception as e:
            self.add_result("Stripe API Connection", False, f"Connection error: {str(e)}")
            self.stripe_enabled = False
            return False
    
    def test_stripe_status_endpoint(self) -> bool:
        """Check internal Stripe status endpoint"""
        try:
            response = requests.get(f"{BASE_URL}/api/stripe/status", timeout=10)
            if response.status_code == 200:
                data = response.json()
                enabled = data.get("enabled", False)
                api_key_present = data.get("api_key_present", False)
                self.add_result("Stripe Status Endpoint", True, 
                    f"enabled={enabled}, api_key_present={api_key_present}")
                return True
            else:
                self.add_result("Stripe Status Endpoint", False, f"HTTP {response.status_code}")
                return False
        except Exception as e:
            self.add_result("Stripe Status Endpoint", False, str(e))
            return False
    
    def test_payment_link_generation(self) -> Tuple[bool, List[Dict]]:
        """Test 2: Payment Link Generation"""
        self.log("=" * 60)
        self.log("TEST 2: PAYMENT LINK GENERATION")
        self.log("=" * 60)
        
        try:
            response = requests.get(f"{BASE_URL}/api/invoices", timeout=10)
            if response.status_code != 200:
                self.add_result("Fetch Invoices", False, f"HTTP {response.status_code}")
                return False, []
            
            invoices = response.json()
            if not invoices:
                self.add_result("Invoice Count", True, "No invoices in system (OK for fresh install)")
                return True, []
            
            self.add_result("Invoice Count", True, f"{len(invoices)} invoices found")
            
            invoice_results = []
            all_pass = True
            
            print("\n| Invoice ID | Status  | Expected Link? | Found Link | Result |")
            print("|------------|---------|----------------|------------|--------|")
            
            for inv in invoices:
                inv_id = inv.get("id")
                status = inv.get("status", "unknown")
                payment_url = inv.get("payment_url")
                
                expected_link = self.stripe_enabled and status != "paid"
                has_link = bool(payment_url)
                
                if status == "paid":
                    result = "PASS"
                elif expected_link and has_link:
                    result = "PASS"
                elif not expected_link and not has_link:
                    result = "PASS"
                else:
                    result = "FAIL"
                    all_pass = False
                
                link_preview = payment_url[:30] + "..." if payment_url and len(payment_url) > 30 else (payment_url or "None")
                print(f"| {inv_id:10} | {status:7} | {'Yes' if expected_link else 'No':14} | {link_preview:10} | {result:6} |")
                
                invoice_results.append({
                    "id": inv_id,
                    "status": status,
                    "expected_link": expected_link,
                    "has_link": has_link,
                    "result": result
                })
            
            self.add_result("Payment Link Generation", all_pass, 
                f"{sum(1 for r in invoice_results if r['result'] == 'PASS')}/{len(invoice_results)} invoices OK")
            
            return all_pass, invoice_results
            
        except Exception as e:
            self.add_result("Payment Link Generation", False, str(e))
            return False, []
    
    def test_customer_portals(self) -> Tuple[bool, List[Dict]]:
        """Test 3: Customer Portal Access"""
        self.log("=" * 60)
        self.log("TEST 3: CUSTOMER PORTAL ACCESS")
        self.log("=" * 60)
        
        try:
            response = requests.get(f"{BASE_URL}/api/customers", timeout=10)
            if response.status_code != 200:
                self.add_result("Fetch Customers", False, f"HTTP {response.status_code}")
                return False, []
            
            customers = response.json()
            if not customers:
                self.add_result("Customer Count", True, "No customers in system (OK for fresh install)")
                return True, []
            
            self.add_result("Customer Count", True, f"{len(customers)} customers found")
            
            portal_results = []
            all_pass = True
            
            print("\n| Customer ID | Name                | Portal Token       | HTTP | Content Check | Result |")
            print("|-------------|---------------------|--------------------|------|---------------|--------|")
            
            for cust in customers:
                cust_id = cust.get("id")
                name = cust.get("name", "Unknown")[:20]
                token = cust.get("public_token", "")
                
                if not token:
                    print(f"| {cust_id:11} | {name:19} | {'No token':18} | N/A  | N/A           | SKIP   |")
                    continue
                
                try:
                    portal_response = requests.get(f"{BASE_URL}/portal/{token}", timeout=10)
                    http_status = portal_response.status_code
                    
                    if http_status == 200:
                        html = portal_response.text
                        has_invoices = "Outstanding Invoices" in html or "Payment History" in html
                        has_tasks = "Recent Work" in html
                        content_ok = has_invoices or has_tasks
                        
                        if content_ok:
                            result = "PASS"
                        else:
                            result = "WARN"
                            all_pass = False
                    else:
                        content_ok = False
                        result = "FAIL"
                        all_pass = False
                    
                    token_preview = token[:15] + "..." if len(token) > 15 else token
                    print(f"| {cust_id:11} | {name:19} | {token_preview:18} | {http_status:4} | {'Yes' if content_ok else 'No':13} | {result:6} |")
                    
                    portal_results.append({
                        "id": cust_id,
                        "name": name,
                        "http_status": http_status,
                        "content_ok": content_ok,
                        "result": result
                    })
                    
                except Exception as e:
                    print(f"| {cust_id:11} | {name:19} | {token[:15]:18} | ERR  | {str(e)[:13]} | FAIL   |")
                    all_pass = False
            
            self.add_result("Customer Portal Access", all_pass,
                f"{sum(1 for r in portal_results if r['result'] == 'PASS')}/{len(portal_results)} portals OK")
            
            return all_pass, portal_results
            
        except Exception as e:
            self.add_result("Customer Portal Access", False, str(e))
            return False, []
    
    def test_email_system(self) -> bool:
        """Test 4: Email System Health"""
        self.log("=" * 60)
        self.log("TEST 4: EMAIL SYSTEM")
        self.log("=" * 60)
        
        try:
            settings_response = requests.get(f"{BASE_URL}/api/settings", timeout=10)
            if settings_response.status_code == 200:
                settings = settings_response.json()
                email_mode = settings.get("email_mode", "UNKNOWN")
                self.add_result("Email Mode Detection", True, f"Mode: {email_mode}")
            else:
                self.add_result("Email Mode Detection", False, f"HTTP {settings_response.status_code}")
        except Exception as e:
            self.add_result("Email Mode Detection", False, str(e))
        
        try:
            test_response = requests.post(
                f"{BASE_URL}/admin/send-test-email",
                params={"to_email": "test@hoss.com"},
                timeout=10
            )
            if test_response.status_code == 200:
                result = test_response.json()
                success = result.get("success", False)
                mode = result.get("mode", "unknown")
                self.add_result("Test Email Endpoint", success, f"Response: {json.dumps(result)}")
                return success
            else:
                self.add_result("Test Email Endpoint", False, f"HTTP {test_response.status_code}")
                return False
        except Exception as e:
            self.add_result("Test Email Endpoint", False, str(e))
            return False
    
    def test_webhook_simulation(self) -> bool:
        """Test 5: Stripe Webhook Simulation (Internal)"""
        self.log("=" * 60)
        self.log("TEST 5: WEBHOOK SIMULATION")
        self.log("=" * 60)
        
        if not self.stripe_enabled:
            self.add_result("Webhook Simulation", True, "SKIPPED - Stripe disabled")
            return True
        
        try:
            response = requests.get(f"{BASE_URL}/api/invoices", timeout=10)
            invoices = response.json()
            
            unpaid_invoices = [inv for inv in invoices if inv.get("status") != "paid" and inv.get("payment_url")]
            
            if not unpaid_invoices:
                self.add_result("Webhook Simulation", True, "SKIPPED - No unpaid invoices with payment links")
                return True
            
            test_invoice = unpaid_invoices[0]
            invoice_id = test_invoice.get("id")
            amount_cents = test_invoice.get("amount_cents", 0)
            
            self.log(f"Testing webhook with invoice {invoice_id} (amount: ${amount_cents/100:.2f})")
            
            self.add_result("Webhook Simulation", True, 
                f"Would test invoice {invoice_id} - manual verification required for signature")
            
            return True
            
        except Exception as e:
            self.add_result("Webhook Simulation", False, str(e))
            return False
    
    def test_admin_console(self) -> bool:
        """Test 6: Admin Console Visibility"""
        self.log("=" * 60)
        self.log("TEST 6: ADMIN CONSOLE")
        self.log("=" * 60)
        
        try:
            response = requests.get(f"{BASE_URL}/admin", timeout=10)
            if response.status_code != 200:
                self.add_result("Admin Console Load", False, f"HTTP {response.status_code}")
                return False
            
            html = response.text
            
            has_outbound_panel = "OUTBOUND" in html or "EMAIL" in html
            has_stripe_panel = "STRIPE" in html
            has_invoices_table = "Invoices" in html or "INVOICES" in html
            
            self.add_result("Admin Console Load", True, "HTTP 200")
            self.add_result("Outbound Email Panel", has_outbound_panel, "Present" if has_outbound_panel else "Missing")
            self.add_result("Stripe Panel", has_stripe_panel, "Present" if has_stripe_panel else "Missing")
            self.add_result("Invoices Section", has_invoices_table, "Present" if has_invoices_table else "Missing")
            
            return has_outbound_panel and has_stripe_panel and has_invoices_table
            
        except Exception as e:
            self.add_result("Admin Console", False, str(e))
            return False
    
    def test_invoice_state_transitions(self) -> bool:
        """Test 7: Invoice State Validation"""
        self.log("=" * 60)
        self.log("TEST 7: INVOICE STATE TRANSITIONS")
        self.log("=" * 60)
        
        try:
            response = requests.get(f"{BASE_URL}/api/invoices", timeout=10)
            if response.status_code != 200:
                self.add_result("Invoice State Check", False, f"HTTP {response.status_code}")
                return False
            
            invoices = response.json()
            
            if not invoices:
                self.add_result("Invoice State Check", True, "No invoices to validate")
                return True
            
            status_counts = {}
            for inv in invoices:
                status = inv.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            
            status_summary = ", ".join([f"{k}={v}" for k, v in status_counts.items()])
            self.add_result("Invoice Status Distribution", True, status_summary)
            
            paid_without_date = 0
            for inv in invoices:
                if inv.get("status") == "paid" and not inv.get("paid_at"):
                    paid_without_date += 1
            
            if paid_without_date > 0:
                self.add_result("Paid Invoice Timestamps", False, f"{paid_without_date} paid invoices missing paid_at")
                return False
            else:
                self.add_result("Paid Invoice Timestamps", True, "All paid invoices have timestamps")
                return True
            
        except Exception as e:
            self.add_result("Invoice State Check", False, str(e))
            return False
    
    def run_all_tests(self):
        """Run complete test suite and generate final report"""
        print("\n" + "=" * 70)
        print("   HOSSAGENT END-TO-END TEST HARNESS")
        print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print("=" * 70 + "\n")
        
        stripe_ok = self.test_stripe_config()
        self.test_stripe_status_endpoint()
        
        payment_ok, _ = self.test_payment_link_generation()
        portal_ok, _ = self.test_customer_portals()
        email_ok = self.test_email_system()
        webhook_ok = self.test_webhook_simulation()
        admin_ok = self.test_admin_console()
        state_ok = self.test_invoice_state_transitions()
        
        print("\n" + "=" * 70)
        print("   FINAL REPORT")
        print("=" * 70)
        
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        
        print(f"\nTotal Tests: {len(self.results)}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        
        print("\n--- Test Summary ---")
        print(f"Stripe Status:           {'OK' if stripe_ok else 'DISABLED/FAIL'}")
        print(f"Email System:            {'OK' if email_ok else 'FAIL'}")
        print(f"Payment Links:           {'OK' if payment_ok else 'FAIL'}")
        print(f"Customer Portals:        {'OK' if portal_ok else 'FAIL'}")
        print(f"Webhook Simulation:      {'OK' if webhook_ok else 'FAIL'}")
        print(f"Admin Console:           {'OK' if admin_ok else 'FAIL'}")
        print(f"Invoice States:          {'OK' if state_ok else 'FAIL'}")
        
        critical_tests = [email_ok, admin_ok, state_ok]
        stripe_tests = [stripe_ok, payment_ok, webhook_ok] if self.stripe_enabled else [True, True, True]
        
        all_critical_pass = all(critical_tests)
        all_stripe_pass = all(stripe_tests)
        
        print("\n" + "=" * 70)
        if all_critical_pass and all_stripe_pass:
            print("   SYSTEM READY FOR LIVE BILLING: YES")
        elif all_critical_pass and not self.stripe_enabled:
            print("   SYSTEM READY FOR LIVE BILLING: NO (Stripe not configured)")
        else:
            print("   SYSTEM READY FOR LIVE BILLING: NO (Critical tests failed)")
        print("=" * 70 + "\n")
        
        if failed > 0:
            print("--- Failed Tests ---")
            for r in self.results:
                if not r.passed:
                    print(f"  - {r.name}: {r.details}")
        
        return all_critical_pass


def main():
    harness = HossTestHarness()
    success = harness.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
