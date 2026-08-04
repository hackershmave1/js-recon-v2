"""
Tests for B-023 - Parameter Signal Extractor
Tests parameter extraction from JS/JSON/XML/HTML content.
"""

import pytest
from unittest.mock import Mock

from app.services.parameter_extractor import ParameterExtractor


class TestParameterExtractor:
    """Test parameter extraction functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.extractor = ParameterExtractor()

    def test_javascript_variable_extraction(self):
        """Test extracting parameter names from JavaScript variables."""
        js_content = """
        const userId = 123;
        let apiKey = "secret";
        var sessionToken = getToken();
        
        function getUserData(username, email) {
            return fetch('/api/user', {
                method: 'POST',
                body: { username, email }
            });
        }
        
        const { name, age } = userProfile;
        """
        
        params = self.extractor.extract(js_content, "test.js", "javascript")
        
        param_names = {p["name"] for p in params}
        assert "userId" in param_names
        assert "apiKey" in param_names
        assert "sessionToken" in param_names
        assert "username" in param_names
        assert "email" in param_names
        assert "name" in param_names
        assert "age" in param_names
    
    def test_javascript_object_properties(self):
        """Test extracting parameter names from object properties."""
        js_content = """
        const config = {
            baseUrl: "https://api.example.com",
            timeout: 5000,
            apiVersion: "v1",
            "auth-token": "bearer123"
        };
        
        user.firstName = "John";
        data.lastLoginTime = new Date();
        """
        
        params = self.extractor.extract(js_content, "config.js", "javascript")
        
        param_names = {p["name"] for p in params}
        assert "baseUrl" in param_names
        assert "timeout" in param_names
        assert "apiVersion" in param_names
        assert "firstName" in param_names
        assert "lastLoginTime" in param_names
    
    def test_json_key_extraction(self):
        """Test extracting parameter names from JSON content."""
        json_content = """{
            "user_id": 123,
            "profile": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "preferences": {
                    "theme": "dark",
                    "notifications": true
                }
            },
            "api_key": "abc123"
        }"""
        
        params = self.extractor.extract(json_content, "data.json", "json")
        
        param_names = {p["name"] for p in params}
        assert "user_id" in param_names
        assert "profile" in param_names
        assert "first_name" in param_names
        assert "last_name" in param_names
        assert "email" in param_names
        assert "preferences" in param_names
        assert "theme" in param_names
        assert "notifications" in param_names
        assert "api_key" in param_names
    
    def test_malformed_json_fallback(self):
        """Test regex fallback for malformed JSON."""
        malformed_json = """
        {
            "valid_key": "value",
            "another_key": "value"
            // invalid comment
            "third_key": "value"
        """
        
        params = self.extractor.extract(malformed_json, "bad.json", "json")
        
        param_names = {p["name"] for p in params}
        assert "valid_key" in param_names
        assert "another_key" in param_names
        assert "third_key" in param_names
    
    def test_xml_element_extraction(self):
        """Test extracting parameter names from XML content."""
        xml_content = """<?xml version="1.0"?>
        <configuration>
            <database>
                <host>localhost</host>
                <port>5432</port>
                <username>admin</username>
                <password>secret</password>
            </database>
            <api_settings enabled="true">
                <base_url>https://api.example.com</base_url>
                <timeout>30</timeout>
                <rate_limit>1000</rate_limit>
            </api_settings>
        </configuration>"""
        
        params = self.extractor.extract(xml_content, "config.xml", "xml")
        
        param_names = {p["name"] for p in params}
        assert "configuration" in param_names
        assert "database" in param_names
        assert "host" in param_names
        assert "port" in param_names
        assert "username" in param_names
        assert "password" in param_names
        assert "api_settings" in param_names
        assert "base_url" in param_names
        assert "timeout" in param_names
        assert "rate_limit" in param_names
        assert "enabled" in param_names  # XML attribute
    
    def test_html_form_extraction(self):
        """Test extracting parameter names from HTML form fields."""
        html_content = """
        <form method="post" action="/login">
            <input type="text" name="username" id="user-input" />
            <input type="email" name="email" id="email-field" />
            <input type="password" name="password" />
            <textarea name="comments" id="comments-box"></textarea>
            <select name="country" id="country-select">
                <option value="us">United States</option>
                <option value="uk">United Kingdom</option>
            </select>
            <input type="hidden" name="csrf_token" value="abc123" />
            <div data-user-id="123" data-session-token="xyz"></div>
        </form>
        """
        
        params = self.extractor.extract(html_content, "form.html", "html")
        
        param_names = {p["name"] for p in params}
        assert "username" in param_names
        assert "email" in param_names
        assert "password" in param_names
        assert "comments" in param_names
        assert "country" in param_names
        assert "csrf_token" in param_names
        assert "user" in param_names  # from data-user-id
        assert "session" in param_names  # from data-session-token
    
    def test_url_parameter_extraction(self):
        """Test extracting parameter names from URL query strings."""
        url = "https://api.example.com/search?q=javascript&category=tutorials&page=1&sort=date&limit=10"
        
        params = self.extractor.extract("", url, None)
        
        param_names = {p["name"] for p in params}
        assert "q" in param_names
        assert "category" in param_names
        assert "page" in param_names
        assert "sort" in param_names
        assert "limit" in param_names
    
    def test_function_parameter_extraction(self):
        """Test extracting function parameter names."""
        js_content = """
        function createUser(name, email, age = 25) {
            return { name, email, age };
        }
        
        const updateProfile = (userId, data) => {
            return api.put(`/users/${userId}`, data);
        };
        
        const handleSubmit = ({username, password}) => {
            authenticate(username, password);
        };
        
        const processData = ([first, second, ...rest]) => {
            console.log(first, second, rest);
        };
        """
        
        params = self.extractor.extract(js_content, "functions.js", "javascript")
        
        param_names = {p["name"] for p in params}
        assert "name" in param_names
        assert "email" in param_names
        assert "age" in param_names
        assert "userId" in param_names
        assert "data" in param_names
        assert "username" in param_names
        assert "password" in param_names
        assert "first" in param_names
        assert "second" in param_names
        assert "rest" in param_names
    
    def test_confidence_scoring(self):
        """Test parameter confidence scoring."""
        js_content = """
        function login(username, password) {
            const api_key = "secret";
            const temp = 123;
            return fetch('/auth', { username, password, api_key });
        }
        """
        
        params = self.extractor.extract(js_content, "auth.js", "javascript")
        
        # Function parameters should have high confidence
        username_param = next(p for p in params if p["name"] == "username")
        assert username_param["confidence"] >= 0.8
        
        # API-related parameters should get bonus confidence
        api_key_param = next(p for p in params if p["name"] == "api_key")
        assert api_key_param["confidence"] >= 0.7
        
        # Generic variables should have lower confidence
        temp_param = next(p for p in params if p["name"] == "temp")
        assert temp_param["confidence"] <= 0.6
    
    def test_false_positive_filtering(self):
        """Test filtering of common JavaScript false positives."""
        js_content = """
        var var_name = "test";  // 'var' should be filtered
        function function_name() {  // 'function' should be filtered
            return true;  // 'true' should be filtered
        }
        const length = array.length;  // 'length' should be filtered
        const validParam = "keep this";  // should be kept
        """
        
        params = self.extractor.extract(js_content, "test.js", "javascript")
        
        param_names = {p["name"] for p in params}
        
        # These should be filtered out
        assert "var" not in param_names
        assert "function" not in param_names
        assert "true" not in param_names
        assert "length" not in param_names
        
        # These should be kept
        assert "validParam" in param_names
    
    def test_content_type_detection(self):
        """Test automatic content type detection."""
        # Test JSON detection
        json_content = '{"test_key": "value"}'
        params = self.extractor.extract(json_content, "file.json")
        assert any(p["source"] == "json" for p in params)
        
        # Test XML detection
        xml_content = '<?xml version="1.0"?><root><element>value</element></root>'
        params = self.extractor.extract(xml_content, "file.xml")
        assert any(p["source"] == "xml" for p in params)
        
        # Test HTML detection
        html_content = '<html><body><input name="test" /></body></html>'
        params = self.extractor.extract(html_content, "file.html")
        assert any(p["source"] == "html" for p in params)
        
        # Test JavaScript detection (default)
        js_content = 'const test = "value";'
        params = self.extractor.extract(js_content, "file.js")
        assert any(p["source"] == "javascript" for p in params)
    
    def test_deduplication(self):
        """Test parameter deduplication with highest confidence preservation."""
        js_content = """
        const username = "test";  // Lower confidence variable
        function login(username, password) {  // Higher confidence parameter
            return username;
        }
        """
        
        params = self.extractor.extract(js_content, "test.js", "javascript")
        
        # Should deduplicate username but keep the higher confidence one
        username_params = [p for p in params if p["name"] == "username"]
        assert len(username_params) == 1
        assert username_params[0]["pattern"] == "function_params"  # Higher confidence source
    
    def test_provenance_tracking(self):
        """Test parameter provenance tracking."""
        js_content = """
        const apiKey = "secret";
        function test(param) {
            return param;
        }
        """
        
        params = self.extractor.extract(js_content, "source.js", "javascript")
        
        for param in params:
            assert param["file"] == "source.js"
            assert "pattern" in param
            assert "source" in param
            assert "context" in param
            if param["line"]:
                assert isinstance(param["line"], int)
                assert param["line"] > 0


class TestParameterExtractorIntegration:
    """Test parameter extractor integration with ComprehensiveExtractor."""
    
    def test_comprehensive_extractor_integration(self):
        """Test parameter extraction through ComprehensiveExtractor."""
        from app.services.comprehensive_extractor import ComprehensiveExtractor
        
        extractor = ComprehensiveExtractor()
        js_content = """
        const config = {
            apiKey: "secret",
            baseUrl: "https://api.example.com"
        };
        
        function authenticate(username, password) {
            return fetch('/auth', {
                method: 'POST',
                headers: { 'API-Key': config.apiKey },
                body: { username, password }
            });
        }
        """
        
        metadata = {"url": "https://wishandwash.co.il/assets/test.js"}
        options = {
            "use_parameter_extraction": True,
            # Keep this test focused on parameter extraction and avoid external sourcemap fetch.
            "include_sourcemap": False,
        }
        
        result = extractor.extract_all(js_content, metadata, options)
        
        assert result["success"] is True
        assert "params" in result["analysis"]
        assert len(result["analysis"]["params"]) > 0
        assert "total_params" in result["stats"]
        
        # Check that parameters were found
        param_names = {p["name"] for p in result["analysis"]["params"]}
        assert "apiKey" in param_names
        assert "baseUrl" in param_names
        assert "username" in param_names
        assert "password" in param_names
    
    def test_parameter_extraction_disabled(self):
        """Test that parameter extraction can be disabled."""
        from app.services.comprehensive_extractor import ComprehensiveExtractor
        
        extractor = ComprehensiveExtractor()
        js_content = "const testParam = 'value';"
        metadata = {"url": "https://wishandwash.co.il/assets/test.js"}
        options = {
            "use_parameter_extraction": False,
            "include_sourcemap": False,
        }
        
        result = extractor.extract_all(js_content, metadata, options)
        
        assert result["success"] is True
        assert "params" in result["analysis"]
        assert len(result["analysis"]["params"]) == 0
        assert result["stats"]["total_params"] == 0
