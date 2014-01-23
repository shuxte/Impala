#!/usr/bin/env python
# Copyright (c) 2012 Cloudera, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Tests the FETCH_FIRST fetch orientation for HS2 clients.
# Impala permits FETCH_FIRST for a particular query iff result caching is enabled
# via the 'impala.resultset.cache.size' confOverlay option. FETCH_FIRST will
# succeed as long all previously fetched rows fit into the bounded result cache.

import pytest
from tests.hs2.hs2_test_suite import HS2TestSuite, needs_session
from cli_service import TCLIService

class TestFetchFirst(HS2TestSuite):
  IMPALA_RESULT_CACHING_OPT = "impala.resultset.cache.size";

  def __test_invalid_result_caching(self, sql_stmt):
    """ Tests that invalid requests for query-result caching fail
    using the given sql_stmt."""
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.statement = sql_stmt
    execute_statement_req.confOverlay = dict()

    # Test that a malformed result-cache size returns an error.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "bad_number"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp,
        TCLIService.TStatusCode.ERROR_STATUS,
        "Invalid value 'bad_number' for 'impala.resultset.cache.size' option")
    self.__verify_num_cached_rows(0)

    # Test that a result-cache size exceeding the per-Impalad maximum returns an error.
    # The default maximum result-cache size is 100000.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "100001"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp,
        TCLIService.TStatusCode.ERROR_STATUS,
        "Requested result-cache size of 100001 exceeds Impala's maximum of 100000")
    self.__verify_num_cached_rows(0)

  def __verify_num_cached_rows(self, num_cached_rows):
    """Asserts that Impala has the given number of rows in its result set cache. Also
    sanity checks the metric for tracking the bytes consumed by the cache."""
    self.impalad_test_service.wait_for_metric_value(
        'impala-server.resultset-cache.total-num-rows', num_cached_rows, timeout=60)
    cached_bytes = self.impalad_test_service.get_metric_value(
        'impala-server.resultset-cache.total-bytes')
    if num_cached_rows > 0:
      assert cached_bytes > 0
    else:
      assert cached_bytes == 0

  @pytest.mark.execute_serially
  @needs_session
  def test_query_stmts(self):
    """Tests Impala's limited support for the FETCH_FIRST fetch orientation for queries.
    Impala permits FETCH_FIRST for a particular query iff result caching is enabled
    via the 'impala.resultset.cache.size' confOverlay option. FETCH_FIRST will succeed as
    long as all previously fetched rows fit into the bounded result cache.
    Regardless of whether a FETCH_FIRST succeeds or not, clients may always resume
    fetching with FETCH_NEXT.
    """
    # Negative tests for the result caching option.
    self.__test_invalid_result_caching("SELECT COUNT(*) FROM functional.alltypes")

    # Test that FETCH_NEXT without result caching succeeds and FETCH_FIRST fails.
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.confOverlay = dict()
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 30"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp)
    for i in xrange(1, 5):
      # Fetch 10 rows with the FETCH_NEXT orientation.
      expected_num_rows = 10
      if i == 4:
        expected_num_rows = 0;
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_NEXT, 10, expected_num_rows)
      # Fetch 10 rows with the FETCH_FIRST orientation, expecting an error.
      # After a failed FETCH_FIRST, the client can still resume FETCH_NEXT.
      self.fetch_fail(execute_statement_resp.operationHandle,
                      TCLIService.TFetchOrientation.FETCH_FIRST,
                      "Restarting of fetch requires enabling of query result caching")
    self.__verify_num_cached_rows(0)
    self.close(execute_statement_resp.operationHandle)

    # Basic test of FETCH_FIRST where the entire result set is cached, and we repeatedly
    # fetch all results.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "30"
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 30"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    for _ in xrange(1, 5):
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_FIRST, 30)
      self.__verify_num_cached_rows(30)
    self.close(execute_statement_resp.operationHandle)

    # Test FETCH_NEXT and FETCH_FIRST where the entire result set does not fit into
    # the cache. FETCH_FIRST will succeed as long as the fetched results
    # fit into the cache.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "29"
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 30"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    # Fetch 10 rows. They fit in the result cache.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10)
    self.__verify_num_cached_rows(10)
    # Restart the fetch and expect success.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 10)
    # Fetch 10 more rows. The result cache has 20 rows total now.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10)
    self.__verify_num_cached_rows(20)
    # Restart the fetch and expect success.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 10)
    self.__verify_num_cached_rows(20)
    # Fetch 10 more rows from the cache.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10)
    self.__verify_num_cached_rows(20)
    # This fetch exhausts the result cache.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10)
    self.__verify_num_cached_rows(0)
    # Since the cache is exhausted, FETCH_FIRST will fail.
    self.fetch_fail(execute_statement_resp.operationHandle,
                    TCLIService.TFetchOrientation.FETCH_FIRST,
                    "The query result cache exceeded its limit of 29 rows. "
                    "Restarting the fetch is not possible")
    self.__verify_num_cached_rows(0)
    # This fetch should succeed but return 0 rows because the stream is eos.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10, 0)
    self.__verify_num_cached_rows(0)
    self.close(execute_statement_resp.operationHandle)

    # Test that FETCH_FIRST serves results from the cache as well as the query
    # coordinator in a single fetch request.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "29"
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 30"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    # Fetch 7 rows. They fit in the result cache.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 7)
    self.__verify_num_cached_rows(7)
    # Restart the fetch asking for 12 rows, 7 of which are served from the cache and 5
    # from the coordinator. The result cache should have 12 rows total now.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 12)
    self.__verify_num_cached_rows(12)
    # Restart the fetch asking for 40 rows. We expect 30 results returned and that the
    # cache is exhausted.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 40, 30)
    self.__verify_num_cached_rows(0)
    # Fetch next should succeed and return 0 rows (eos).
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 7, 0)
    self.__verify_num_cached_rows(0)
    # Since the cache is exhausted, FETCH_FIRST will fail.
    self.fetch_fail(execute_statement_resp.operationHandle,
                    TCLIService.TFetchOrientation.FETCH_FIRST,
                    "The query result cache exceeded its limit of 29 rows. "
                    "Restarting the fetch is not possible")
    self.__verify_num_cached_rows(0)
    self.close(execute_statement_resp.operationHandle)

    # Test that resuming FETCH_NEXT after a failed FETCH_FIRST works.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "10"
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 30"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    # Fetch 9 rows. They fit in the result cache.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 9)
    self.__verify_num_cached_rows(9)
    # Fetch 9 rows. Cache is exhausted now.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 9)
    self.__verify_num_cached_rows(0)
    # Restarting the fetch should fail.
    self.fetch_fail(execute_statement_resp.operationHandle,
                    TCLIService.TFetchOrientation.FETCH_FIRST,
                    "The query result cache exceeded its limit of 10 rows. "
                    "Restarting the fetch is not possible")
    self.__verify_num_cached_rows(0)
    # Resuming FETCH_NEXT should succeed. There are 12 remaining rows to fetch.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 100, 12)
    self.__verify_num_cached_rows(0)
    self.close(execute_statement_resp.operationHandle)

  @pytest.mark.execute_serially
  @needs_session
  def test_constant_query_stmts(self):
    """Tests query stmts that return a constant result set. These queries are handled
    somewhat specially by Impala, therefore, we test them separately. We expect
    FETCH_FIRST to always succeed if result caching is enabled."""
    # Tests a query with limit 0.
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.confOverlay = dict()
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "10"
    execute_statement_req.statement =\
      "SELECT * FROM functional.alltypessmall ORDER BY id LIMIT 0"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    for i in xrange(0, 3):
      # Fetch some rows. Expect to get 0 rows.
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_NEXT, i * 10, 0)
      self.__verify_num_cached_rows(0)
      # Fetch some rows with FETCH_FIRST. Expect to get 0 rows.
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_FIRST, i * 10, 0)
      self.__verify_num_cached_rows(0)
    self.close(execute_statement_resp.operationHandle)

    # Tests a constant select.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "10"
    execute_statement_req.statement = "SELECT 1, 1.0, 'a', trim('abc'), NULL"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    # Fetch 100 rows with FETCH_FIRST. Expect to get 1 row.
    self.fetch(execute_statement_resp.operationHandle,
              TCLIService.TFetchOrientation.FETCH_FIRST, 100, 1)
    self.__verify_num_cached_rows(1)
    for i in xrange(0, 3):
      # Fetch some rows with FETCH_FIRST. Expect to get 1 row.
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_FIRST, i * 10, 1)
      self.__verify_num_cached_rows(1)
      # Fetch some more rows. Expect to get 1 row.
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_NEXT, i * 10, 0)
      self.__verify_num_cached_rows(1)
    self.close(execute_statement_resp.operationHandle)

  @pytest.mark.execute_serially
  @needs_session
  def test_non_query_stmts(self):
    """Tests Impala's limited support for the FETCH_FIRST fetch orientation for
    non-query stmts that return a result set, such as SHOW, COMPUTE STATS, etc.
    The results of non-query statements are always cached entirely, and therefore,
    the cache can never be exhausted, i.e., FETCH_FIRST should always succeed.
    However, we only allow FETCH_FIRST on non-query stmts if query caching was enabled
    by the client for consistency. We use a 'show stats' stmt as a representative
    of these types of non-query stmts.
    """
    # Negative tests for the result caching option.
    self.__test_invalid_result_caching("show table stats functional.alltypes")

    # Test that FETCH_NEXT without result caching succeeds and FETCH_FIRST fails.
    # The show stmt returns exactly 25 results.
    execute_statement_req = TCLIService.TExecuteStatementReq()
    execute_statement_req.sessionHandle = self.session_handle
    execute_statement_req.confOverlay = dict()
    execute_statement_req.statement = "show table stats functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp)
    for i in xrange(1, 5):
      # Fetch 10 rows with the FETCH_NEXT orientation.
      expected_num_rows = 10
      if i == 3:
        expected_num_rows = 5
      if i == 4:
        expected_num_rows = 0
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_NEXT, 10, expected_num_rows)
      # Fetch 10 rows with the FETCH_FIRST orientation, expecting an error.
      # After a failed FETCH_FIRST, the client can still resume FETCH_NEXT.
      self.fetch_fail(execute_statement_resp.operationHandle,
                      TCLIService.TFetchOrientation.FETCH_FIRST,
                      "Restarting of fetch requires enabling of query result caching")
      # The results of non-query stmts are not counted as 'cached'.
      self.__verify_num_cached_rows(0)

    # Tests that FETCH_FIRST always succeeds as long as result caching is enabled.
    # The show stmt returns exactly 25 results. The cache cannot be exhausted.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "1"
    execute_statement_req.statement = "show table stats functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp)
    for _ in xrange(1, 5):
      self.fetch(execute_statement_resp.operationHandle,
                 TCLIService.TFetchOrientation.FETCH_FIRST, 30, 25)
    # The results of non-query stmts are not counted as 'cached'.
    self.__verify_num_cached_rows(0)

    # Test combinations of FETCH_FIRST and FETCH_NEXT.
    # The show stmt returns exactly 25 results.
    execute_statement_req.confOverlay[self.IMPALA_RESULT_CACHING_OPT] = "1"
    execute_statement_req.statement = "show table stats functional.alltypes"
    execute_statement_resp = self.hs2_client.ExecuteStatement(execute_statement_req)
    HS2TestSuite.check_response(execute_statement_resp)
    # Fetch 10 rows.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 10)
    # Restart the fetch asking for 20 rows.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 20)
    # FETCH_NEXT asking for 100 rows. There are only 5 remaining rows.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 100, 5)
    # Restart the fetch asking for 10 rows.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_FIRST, 5)
    # FETCH_NEXT asking for 100 rows. There are only 20 remaining rows.
    self.fetch(execute_statement_resp.operationHandle,
               TCLIService.TFetchOrientation.FETCH_NEXT, 100, 20)
