        # Fetch market cap and transaction value as separate calls
        cap_mrkt_cur_usd = self.fetch_metrics("CapMrktCurUSD")
        tx_tfr_val_adj_usd = self.fetch_metrics("TxTfrValAdjUSD")
        # Combine the results as needed
        combined_result = (cap_mrkt_cur_usd, tx_tfr_val_adj_usd)
        return combined_result
