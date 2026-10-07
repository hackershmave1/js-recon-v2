// Vite + React, unminified. The "URL constants module" pattern.
// Base is a plain string literal -> fully const-foldable.
import axios from "axios";

const ApiUrl = "https://apigatewayazeu-dev.accenture.com/idvs/mfe/dev/v1.0";
const LegacyApi = import.meta.env.VITE_LEGACY_API || "https://legacy-idvs.accenture.com/api";

export const getAssignedQueueURL = `${ApiUrl}/AssignedQueue?`;
export const getQueueGridPaginationURL = `${ApiUrl}/DocumentDetailsPagination?`;
export const getAgentDashboardURL = `${ApiUrl}/AgenticAI/GetFilteredDocumentDetails`;
export const getAuthConfigurationURL = `${ApiUrl}/Authentication`;
export const getScopeConfigurationURL = `${ApiUrl}/Authentication/AIG?ClientId=`;
export const updateEULADetailsURL = `${ApiUrl}/GDPR`;
export const getFieldHistoryURL = `${ApiUrl}/FieldHistory`;
export const uploadDocumentURL = `${LegacyApi}/Document/Upload`;

export async function fetchQueue(queueId, page) {
  return axios.get(`${getAssignedQueueURL}QueueId=${queueId}&Page=${page}`);
}

export async function acceptEula(payload) {
  return axios.put(updateEULADetailsURL, payload);
}

export function pingHealth() {
  return fetch(ApiUrl + "/health", { method: "HEAD" });
}

export function loadDashboard(filters) {
  return axios({ method: "post", url: getAgentDashboardURL, data: filters });
}
