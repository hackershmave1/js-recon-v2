# WishAndWash.co.il - JavaScript Security Analysis Report

## 🎯 Executive Summary

**Target**: https://wishandwash.co.il/assets/index-BDSyL5Fh.js  
**File Size**: 623,273 bytes (0.6MB)  
**Architecture**: React.js frontend with Supabase Backend-as-a-Service  
**Analysis Date**: 2026-02-09

## 🏗️ Application Architecture

### Backend Infrastructure
- **Primary Backend**: Supabase (Backend-as-a-Service)
- **Supabase Instance**: `uldslbepxntouegfzody.supabase.co`
- **Database**: PostgreSQL (managed by Supabase)
- **Storage**: Supabase Storage + Browser localStorage/sessionStorage
- **Realtime**: Supabase Realtime (WebSocket-based)
- **Frontend**: React.js 19.2.1 with modern hooks

### Authentication Architecture
- **Method**: JWT-based authentication via Supabase Auth
- **Token Storage**: Browser localStorage/sessionStorage
- **Session Management**: Auto-refresh tokens with 30-minute idle timeout
- **Multi-factor Auth**: TOTP support detected
- **Social Auth**: Web3 wallet integration (Ethereum/Solana)
- **Auth Flow**: PKCE (Proof Key for Code Exchange) for security

## 🔍 Discovered API Endpoints

### Core Supabase Endpoints
```
Base URL: https://uldslbepxntouegfzody.supabase.co
```

#### Authentication Endpoints
- `/auth/v1/token` - Token refresh
- `/auth/v1/user` - User profile
- `/auth/v1/logout` - Sign out
- `/auth/v1/signup` - User registration
- `/auth/v1/recover` - Password recovery
- `/auth/v1/verify` - Email/phone verification

#### Admin API (Requires elevated privileges)
- `/auth/v1/admin/users` - User management
- `/auth/v1/admin/users/{id}` - Individual user operations

#### Database API (PostgREST)
- `/rest/v1/` - Auto-generated REST API from database schema
- `/rest/v1/{table_name}` - Table operations (CRUD)

#### Storage API
- `/storage/v1/` - File storage operations
- `/storage/v1/bucket/{bucket_name}` - Bucket operations

#### Realtime API
- `/realtime/v1/websocket` - WebSocket connection for live data
- `/api/broadcast` - Real-time broadcasting

## 🔐 Authentication & Authorization

### How Authentication Works
1. **Login Process**: User provides credentials → Supabase Auth validates → Returns JWT access_token + refresh_token
2. **Token Storage**: Tokens stored in browser localStorage with key pattern `supabase.auth.token`
3. **API Authorization**: All requests include `Authorization: Bearer <access_token>` header
4. **Token Refresh**: Automatic refresh using refresh_token when access_token expires
5. **Session Persistence**: Sessions persist across browser restarts via localStorage

### Authentication Headers
```javascript
Authorization: Bearer <jwt_access_token>
apikey: <supabase_anon_key>
```

### Storage Keys Found
- `supabase.auth.token` - JWT tokens
- `supabase.gotrue-js.locks.debug` - Debug locks
- `yeshiva-logos` - Application-specific data

## 🚪 Attack Vectors & Security Considerations

### 1. Supabase Instance Enumeration
- **Target**: `uldslbepxntouegfzody.supabase.co`
- **Attack**: Direct API access to enumerate database schema
- **Method**: `GET /rest/v1/` with various table guesses

### 2. JWT Token Exploitation
- **Storage Location**: Browser localStorage
- **XSS Risk**: Tokens accessible via JavaScript
- **Attack**: Steal tokens via XSS → Impersonate user

### 3. Row-Level Security (RLS) Bypass
- **Supabase Feature**: Database-level access control
- **Attack**: Test for improperly configured RLS policies
- **Method**: Attempt cross-user data access via API

### 4. Storage Bucket Enumeration
- **Endpoint**: `/storage/v1/bucket/{bucket_name}`
- **Attack**: Enumerate and access file buckets
- **Method**: Brute force common bucket names

### 5. Realtime Channel Hijacking
- **Feature**: Live data subscriptions
- **Attack**: Subscribe to unauthorized channels
- **Method**: Websocket connection with channel enumeration

## 🛠️ How to Interact with the Backend

### 1. Obtain Supabase Configuration
```javascript
// From the analysis, the app uses:
const supabaseUrl = 'https://uldslbepxntouegfzody.supabase.co'
const supabaseKey = '[ANON_KEY_EXTRACTED_FROM_JS]'
```

### 2. Authentication Methods

#### Option A: Valid User Credentials
```bash
# Login to get valid JWT
curl -X POST https://uldslbepxntouegfzody.supabase.co/auth/v1/token \
  -H "Content-Type: application/json" \
  -H "apikey: [ANON_KEY]" \
  -d '{"email":"user@example.com","password":"password","grant_type":"password"}'
```

#### Option B: Anonymous Access (if enabled)
```bash
# Use anonymous key directly
curl -X GET https://uldslbepxntouegfzody.supabase.co/rest/v1/[table] \
  -H "apikey: [ANON_KEY]" \
  -H "Authorization: Bearer [ANON_KEY]"
```

### 3. Database Interaction
```bash
# List available tables/endpoints
curl -X GET https://uldslbepxntouegfzody.supabase.co/rest/v1/ \
  -H "apikey: [ANON_KEY]" \
  -H "Authorization: Bearer [JWT_TOKEN]"

# Query specific table
curl -X GET https://uldslbepxntouegfzody.supabase.co/rest/v1/[table_name] \
  -H "apikey: [ANON_KEY]" \
  -H "Authorization: Bearer [JWT_TOKEN]"
```

### 4. Storage Access
```bash
# List storage buckets
curl -X GET https://uldslbepxntouegfzody.supabase.co/storage/v1/bucket \
  -H "Authorization: Bearer [JWT_TOKEN]"

# Access files
curl -X GET https://uldslbepxntouegfzody.supabase.co/storage/v1/object/[bucket]/[file] \
  -H "Authorization: Bearer [JWT_TOKEN]"
```

## 🔎 Security Findings Summary

### Critical
- **3,071 potential secrets/keys** embedded in code (needs manual verification)
- **JWT tokens in localStorage** - XSS vulnerability risk
- **Supabase instance exposed** - Direct database API access

### Medium
- **4 innerHTML assignments** - Potential XSS vectors
- **localStorage usage** - Client-side data exposure
- **WebSocket realtime** - Channel enumeration possible

### Low
- **External domains referenced** - Supply chain risks
- **React devtools fingerprinting** - Information disclosure

## 🎯 Recommended Testing Approach

1. **Extract Supabase Keys**: Find the actual `anon` key in the JavaScript bundle
2. **Schema Enumeration**: Test database endpoints for accessible tables
3. **Authentication Bypass**: Test for weak RLS policies
4. **File Storage Testing**: Enumerate and test bucket permissions
5. **Realtime Testing**: Test WebSocket channel subscriptions
6. **XSS Testing**: Look for client-side injection points that could steal tokens

## 📝 Notes

This analysis is based on static JavaScript code analysis. Dynamic testing would reveal the actual API behavior, database schema, and security controls in place. The Supabase instance appears to be production-configured with proper authentication requirements.