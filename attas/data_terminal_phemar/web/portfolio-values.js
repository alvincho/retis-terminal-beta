/** Portfolio Review presentation bridge. Every valuation comes from an agent. */
export function portfolioValues(response) {
 if(response?.schema!=='attas:portfolio-review-valuation/1'||!response.data||!Array.isArray(response.data.values))throw new Error('Portfolio valuation requires the Calculator result.');
 return response.data;
}
