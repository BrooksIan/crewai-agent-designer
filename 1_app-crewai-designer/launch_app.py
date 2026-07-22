# ###########################################################################
#
# CLOUDERA APPLIED MACHINE LEARNING PROTOTYPE (AMP)
# CrewAI Agent Designer — launch Streamlit on the CML application port.
#
# ###########################################################################

!streamlit run app/streamlit_app.py --server.port $CDSW_APP_PORT --server.address 127.0.0.1 --server.enableCORS false --server.enableXsrfProtection false --browser.gatherUsageStats false
