import tabula
import pandas as pd

new = True
pdf_file = '/Users/dwillis/code/openelections-sources-mi/2020/general/Midland MI Results per Precinct Data report.pdf'
#unable to get the race from the first page, so we have to manually add it in
race_name = 'United States Senator'
precinct = []
candidate = []
votes = []
race = []
start_page = 1
end_page = 7
while start_page <= end_page:
    data = tabula.read_pdf(pdf_file,multiple_tables=True,lattice=False,stream=False,pages=(start_page))
    new = False
    for x in data:
        df = x
        #since the first page has the candidates in the headers, we need to handle this page differently then the rest.  Here we need to strip out the header data to add to the candidate list
        print(start_page)
        if start_page == 1:
            df.drop(df.columns[0],axis=1,inplace=True)
            for index, row in df.iterrows():
                for i in range(len(df.columns.values)):
                    if i > 1:
                        candidate.append(df.columns.values[i])
                        votes.append(df.iloc[index,i])
                        precinct.append(df.iloc[index,0])
                        race.append(race_name)
        else:
            df.drop(df.columns[0],axis=1,inplace=True)
            #an array of row indexes to skip and not process.
            skip_rows = []
            for index, rows in df.iterrows():
                #check to see if the first value is 'Precinct'.  If it is, then this will add the values of that row into candidatelist array.
                if rows[0] == 'Precinct':
                    skip_rows.append(index)
                    count = 1
                    candidateList = []
                    while count < len(df.columns.values):
                        #specific use cases for the midland county results.  Several pages, had combined rows together that I need to split apart.
                        if str(rows[count]).count('Party') == 2:
                            candidateList.append('Green Party')
                            candidateList.append('Natural Law Party')
                        elif 'Amy Slepr Total' in str(rows[count]):
                            candidateList.append('Amy Slepr')
                            candidateList.append('Total Write-in')
                        elif 'Melissa Noelle Lambert Total ' in str(rows[count]):
                            candidateList.append('Melissa Noelle Lambert')
                            candidateList.append('Total Write-in')
                        elif 'Fuente/Darcy Total' in str(rows[count]):
                            candidateList.append('Rocky De La Fuente/Darcy')
                            candidateList.append('Total Write-in')
                        else:
                            candidateList.append(rows[count])
                        count = count + 1
                    #single case issue, where the names of the candidates are broken across two lines.  This combines the lines in the candidatelist array.
                    if not pd.notnull(df.iloc[index+1,0]):
                        skip_rows.append(index+1)
                        count = 0
                        while count < len(df.columns.values)-1:
                            if pd.notnull(df.iloc[index+1,count+1]):
                                candidateList[count] = candidateList[count]+' '+df.iloc[index+1,count+1]
                            count = count + 1
                #only runs if we have to process the data.
                if index not in skip_rows:
                    for i in range(len(df.columns.values)):
                        if i >= 1:
                            if i == len(df.columns.values)-1:
                                pass
                            if not pd.notnull(df.iloc[index,i]):
                                pass
                            else:
                                race.append(race_name)
                                #several pages combined the results into one column, this will split that into two columns and store the data correctly.
                                if ' ' in str(df.iloc[index,i]):
                                    votes.append(df.iloc[index,i].split(' ')[0])
                                    votes.append(df.iloc[index,i].split(' ')[1])
                                    candidate.append(candidateList[-2])
                                    candidate.append(candidateList[-1])
                                    precinct.append(df.iloc[index,0])
                                    race.append(race_name)
                                else:
                                    votes.append(df.iloc[index,i])
                                    candidate.append(candidateList[i-1])
                                precinct.append(df.iloc[index,0])
                if rows[0] == 'Total' and index != len(df)-1:
                    skip_rows.append(index+1)
                    race_name = df.iloc[index+1,0]
                    new = True

    start_page = start_page+1

final = {'precinct':precinct,'race':race,'candidate':candidate,'votes':votes}
new = pd.DataFrame(final)
new.to_csv('20201103_mi_general_midland_precinct.csv')
