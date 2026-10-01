// SPDX-License-Identifier: MIT
// First-party unshifted Householder factor of original principal A transpose.
#pragma once
#include <vector>
#include <cmath>
#include <limits>
#include <cstdint>

class PrSectionQr
{
    uint32_t m_rows=0,m_columns=0;
    std::vector<double> m_data,m_tau;
    double& at(uint32_t row,uint32_t column) { return m_data[size_t(column)*m_rows+row]; }
    double at(uint32_t row,uint32_t column) const { return m_data[size_t(column)*m_rows+row]; }
    bool reflect(std::vector<double>& value,uint32_t column) const
    {
        double dot=value[column];
        for(uint32_t row=column+1;row<m_rows;++row) dot+=at(row,column)*value[row];
        dot*=m_tau[column];
        if(!std::isfinite(dot)) return false;
        value[column]-=dot;
        for(uint32_t row=column+1;row<m_rows;++row) value[row]-=at(row,column)*dot;
        return true;
    }
public:
    static bool dimensions(uint32_t rows,uint32_t columns)
    {
        return columns>0 && rows>=columns && uint64_t(rows)*columns<=
            std::numeric_limits<size_t>::max()/sizeof(double);
    }
    bool build(uint32_t rows,uint32_t columns,std::vector<double>&& input)
    {
        if(!dimensions(rows,columns) || input.size()!=size_t(rows)*columns) return false;
        m_rows=rows; m_columns=columns; m_data=std::move(input); m_tau.assign(columns,0);
        for(double value:m_data) if(!std::isfinite(value)) return false;
        for(uint32_t column=0;column<columns;++column)
        {
            double norm=0;
            for(uint32_t row=column;row<rows;++row) norm=std::hypot(norm,at(row,column));
            const double alpha=at(column,column),beta=-std::copysign(norm,alpha);
            const double denominator=alpha-beta;
            if(!std::isfinite(beta) || beta==0 || !std::isfinite(denominator) || denominator==0) return false;
            m_tau[column]=(beta-alpha)/beta;
            if(!std::isfinite(m_tau[column])) return false;
            for(uint32_t row=column+1;row<rows;++row) at(row,column)/=denominator;
            at(column,column)=beta;
            for(uint32_t next=column+1;next<columns;++next)
            {
                double dot=at(column,next);
                for(uint32_t row=column+1;row<rows;++row) dot+=at(row,column)*at(row,next);
                dot*=m_tau[column];
                if(!std::isfinite(dot)) return false;
                at(column,next)-=dot;
                for(uint32_t row=column+1;row<rows;++row) at(row,next)-=at(row,column)*dot;
            }
        }
        for(double value:m_data) if(!std::isfinite(value)) return false;
        return true;
    }
    bool apply(std::vector<double>& value) const
    {
        if(value.size()!=m_rows) return false;
        for(uint32_t column=0;column<m_columns;++column) if(!reflect(value,column)) return false;
        // Leading inverse is R^-T R^-1. Orthogonal complement is identity.
        for(uint32_t i=m_columns;i>0;--i)
        {
            const uint32_t row=i-1;
            for(uint32_t column=row+1;column<m_columns;++column) value[row]-=at(row,column)*value[column];
            value[row]/=at(row,row);
        }
        for(uint32_t row=0;row<m_columns;++row)
        {
            for(uint32_t column=0;column<row;++column) value[row]-=at(column,row)*value[column];
            value[row]/=at(row,row);
        }
        for(uint32_t column=m_columns;column>0;--column) if(!reflect(value,column-1)) return false;
        for(double item:value) if(!std::isfinite(item)) return false;
        return true;
    }
    double bytes() const { return double((m_data.capacity()+m_tau.capacity())*sizeof(double)); }
    double blocks() const { return double((m_rows+5)/6)*double((m_columns+5)/6); }
    double flops() const { return 2.*m_rows*m_columns*m_columns-2.*m_columns*m_columns*m_columns/3.; }
};
